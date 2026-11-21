#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <libusb-1.0/libusb.h>
#include <libuvc/libuvc.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_CAMERAS 4

struct camera_target {
    const char *port_path;
    const char *sysfs_path;
    const char *name;
    uint8_t ports[2];
    size_t port_count;
};

struct camera {
    const struct camera_target *target;
    uint8_t address;
    uvc_device_handle_t *handle;
    uvc_stream_handle_t *stream;
    uvc_frame_t *decoded;
    unsigned width;
    unsigned height;
    unsigned frames;
    unsigned bad;
    int touched;
    int frames_mode;
    uint8_t saved_ae_mode;
    uint8_t saved_ae_priority;
    uint32_t saved_exposure;
    uint16_t saved_gain;
    unsigned saved_controls;
    unsigned changed_controls;
    int controls_restore_failed;
};

struct stream_options {
    unsigned fps;
    int exposure;
    int gain;
    int ae_priority;
};

static const struct camera_target camera_targets[] = {
    {
        .port_path = "1", .sysfs_path = "3-1", .name = "camera_port_1",
        .ports = {1}, .port_count = 1
    },
    {
        .port_path = "2.2", .sysfs_path = "3-2.2", .name = "wrist_port_2_2",
        .ports = {2, 2}, .port_count = 2
    },
    {
        .port_path = "2.3", .sysfs_path = "3-2.3", .name = "wrist_port_2_3",
        .ports = {2, 3}, .port_count = 2
    },
    {
        .port_path = "2.4", .sysfs_path = "3-2.4", .name = "camera_port_2_4",
        .ports = {2, 4}, .port_count = 2
    }
};

static _Atomic int stopping = ATOMIC_VAR_INIT(0);
static _Atomic int stop_reason = ATOMIC_VAR_INIT(0);
static pthread_mutex_t output_mutex = PTHREAD_MUTEX_INITIALIZER;

_Static_assert(ATOMIC_INT_LOCK_FREE == 2,
    "signal-safe stop state requires lock-free atomic int");

#define FRAME_WRITE_TIMEOUT_NS UINT64_C(2000000000)
#define FRAME_WRITE_POLL_SLICE_MS 100

enum stop_reason {
    STOP_NONE,
    STOP_SIGNAL,
    STOP_STDIN_EOF,
    STOP_BAD_FRAME,
    STOP_OUTPUT,
    STOP_INPUT
};

enum write_result {
    WRITE_ERROR = -1,
    WRITE_OK = 0,
    WRITE_STOPPED = 1
};

static double monotonic_seconds(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec + now.tv_nsec / 1e9;
}

static int monotonic_ns(uint64_t *value)
{
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now))
        return -1;
    *value = (uint64_t)now.tv_sec * UINT64_C(1000000000) +
        (uint64_t)now.tv_nsec;
    return 0;
}

static uint64_t timespec_ns(const struct timespec *value)
{
    return (uint64_t)value->tv_sec * UINT64_C(1000000000) +
        (uint64_t)value->tv_nsec;
}

static int stop_requested(void)
{
    return atomic_load_explicit(&stopping, memory_order_acquire);
}

static int current_stop_reason(void)
{
    return atomic_load_explicit(&stop_reason, memory_order_relaxed);
}

static int stopped_result(void)
{
    int reason = current_stop_reason();
    if (reason == STOP_STDIN_EOF)
        return 0;
    if (reason == STOP_SIGNAL)
        return 130;
    return 1;
}

static void request_stop(enum stop_reason reason)
{
    int expected = STOP_NONE;
    atomic_compare_exchange_strong_explicit(&stop_reason, &expected, reason,
        memory_order_relaxed, memory_order_relaxed);
    atomic_store_explicit(&stopping, 1, memory_order_release);
}

static void stop_signal(int signum)
{
    int expected = STOP_NONE;
    int reason = signum == SIGPIPE ? STOP_OUTPUT : STOP_SIGNAL;
    atomic_compare_exchange_strong_explicit(&stop_reason, &expected, reason,
        memory_order_relaxed, memory_order_relaxed);
    atomic_store_explicit(&stopping, 1, memory_order_release);
}

static void locked_fprintf(FILE *stream, const char *format, ...)
{
    va_list arguments;
    pthread_mutex_lock(&output_mutex);
    va_start(arguments, format);
    vfprintf(stream, format, arguments);
    va_end(arguments);
    fflush(stream);
    pthread_mutex_unlock(&output_mutex);
}

static unsigned read_controls(struct camera *camera, const char *stage,
    uint8_t *mode, uint8_t *priority, uint32_t *exposure, uint16_t *gain)
{
    unsigned mask = 0;
    if (uvc_get_ae_mode(camera->handle, mode, UVC_GET_CUR) == UVC_SUCCESS)
        mask |= 1;
    if (uvc_get_ae_priority(camera->handle, priority, UVC_GET_CUR) == UVC_SUCCESS)
        mask |= 2;
    if (uvc_get_exposure_abs(camera->handle, exposure, UVC_GET_CUR) == UVC_SUCCESS)
        mask |= 4;
    if (uvc_get_gain(camera->handle, gain, UVC_GET_CUR) == UVC_SUCCESS)
        mask |= 8;
    locked_fprintf(stderr,
        "{\"event\":\"controls\",\"camera\":\"%s\",\"stage\":\"%s\","
        "\"ae_mode\":%d,\"ae_priority\":%d,\"exposure_100us\":%d,"
        "\"gain\":%d,\"read_mask\":%u}\n",
        camera->target->name, stage, mask & 1 ? (int)*mode : -1,
        mask & 2 ? (int)*priority : -1, mask & 4 ? (int)*exposure : -1,
        mask & 8 ? (int)*gain : -1, mask);
    return mask;
}

static int apply_controls(struct camera *camera, const struct stream_options *options)
{
    camera->saved_controls = read_controls(camera, "before",
        &camera->saved_ae_mode, &camera->saved_ae_priority,
        &camera->saved_exposure, &camera->saved_gain);
    if (options->exposure >= 0) {
        uint32_t minimum = 0, maximum = 0;
        if ((camera->saved_controls & 5) != 5 ||
            uvc_get_exposure_abs(camera->handle, &minimum, UVC_GET_MIN) ||
            uvc_get_exposure_abs(camera->handle, &maximum, UVC_GET_MAX) ||
            (unsigned)options->exposure < minimum || (unsigned)options->exposure > maximum)
            return -1;
        camera->changed_controls |= 1;
        if (uvc_set_ae_mode(camera->handle, 1) ||
            uvc_set_exposure_abs(camera->handle, (uint32_t)options->exposure))
            return -1;
    }
    if (options->gain >= 0) {
        uint16_t minimum = 0, maximum = 0;
        if (!(camera->saved_controls & 8) ||
            uvc_get_gain(camera->handle, &minimum, UVC_GET_MIN) ||
            uvc_get_gain(camera->handle, &maximum, UVC_GET_MAX) ||
            (unsigned)options->gain < minimum || (unsigned)options->gain > maximum)
            return -1;
        camera->changed_controls |= 4;
        if (uvc_set_gain(camera->handle, (uint16_t)options->gain))
            return -1;
    }
    int priority = options->ae_priority;
    if (priority < 0 && options->exposure >= 0)
        priority = 0;
    if (priority >= 0) {
        if (!(camera->saved_controls & 2))
            return -1;
        camera->changed_controls |= 2;
        if (uvc_set_ae_priority(camera->handle, (uint8_t)priority))
            return -1;
    }
    uint8_t mode = 0, selected_priority = 0;
    uint32_t exposure = 0;
    uint16_t gain = 0;
    unsigned mask = read_controls(camera, "applied", &mode, &selected_priority, &exposure, &gain);
    if ((options->exposure >= 0 && ((mask & 5) != 5 || mode != 1 ||
                                  exposure != (unsigned)options->exposure)) ||
        (options->gain >= 0 && (!(mask & 8) || gain != (unsigned)options->gain)) ||
        (priority >= 0 && (!(mask & 2) || selected_priority != (unsigned)priority)))
        return -1;
    return 0;
}

static int restore_controls(struct camera *camera)
{
    int failed = 0;
    uint8_t mode = 0, priority = 0;
    uint32_t exposure = 0;
    uint16_t gain = 0;
    read_controls(camera, "after_stream", &mode, &priority, &exposure, &gain);
    if (camera->changed_controls & 1) {
        if (uvc_set_ae_mode(camera->handle, 1))
            failed = 1;
        if (uvc_set_exposure_abs(camera->handle, camera->saved_exposure))
            failed = 1;
        if (uvc_set_ae_mode(camera->handle, camera->saved_ae_mode))
            failed = 1;
    }
    if ((camera->changed_controls & 4) &&
        uvc_set_gain(camera->handle, camera->saved_gain))
        failed = 1;
    if ((camera->changed_controls & 2) &&
        uvc_set_ae_priority(camera->handle, camera->saved_ae_priority))
        failed = 1;
    unsigned mask = read_controls(camera, "restored", &mode, &priority, &exposure, &gain);
    if (((camera->changed_controls & 1) && (!(mask & 1) || mode != camera->saved_ae_mode)) ||
        ((camera->changed_controls & 2) && (!(mask & 2) || priority != camera->saved_ae_priority)) ||
        ((camera->changed_controls & 4) && (!(mask & 8) || gain != camera->saved_gain)))
        failed = 1;
    return failed;
}

static int set_nonblocking(int fd, int *saved_flags)
{
    int flags = fcntl(fd, F_GETFL);
    if (flags < 0)
        return -1;
    *saved_flags = flags;
    if (!(flags & O_NONBLOCK) && fcntl(fd, F_SETFL, flags | O_NONBLOCK))
        return -1;
    return 0;
}

static void restore_flags(int fd, int saved_flags)
{
    if (saved_flags >= 0)
        (void)fcntl(fd, F_SETFL, saved_flags);
}

static int read_parent_status(int fd)
{
    char discard[256];
    ssize_t bytes = read(fd, discard, sizeof discard);
    if (bytes > 0)
        return 0;
    if (bytes == 0)
        return 1;
    if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)
        return 0;
    return -1;
}

static int parent_event_status(int fd, short revents)
{
    if (revents & POLLNVAL) {
        errno = EBADF;
        return -1;
    }
    int status = read_parent_status(fd);
    if (status == 0 && (revents & POLLERR)) {
        errno = EIO;
        return -1;
    }
    return status;
}

static int write_abort_requested(void)
{
    if (!stop_requested())
        return 0;
    return current_stop_reason() != STOP_BAD_FRAME;
}

static int bounded_poll_timeout(uint64_t deadline_ns)
{
    uint64_t now_ns;
    if (monotonic_ns(&now_ns))
        return -1;
    if (now_ns >= deadline_ns) {
        errno = ETIMEDOUT;
        return -1;
    }
    uint64_t remaining_ns = deadline_ns - now_ns;
    uint64_t timeout_ms =
        (remaining_ns + UINT64_C(999999)) / UINT64_C(1000000);
    if (timeout_ms > FRAME_WRITE_POLL_SLICE_MS)
        timeout_ms = FRAME_WRITE_POLL_SLICE_MS;
    return (int)timeout_ms;
}

static enum write_result write_all_bounded(int output_fd, int parent_fd,
    const void *buffer, size_t length, uint64_t deadline_ns)
{
    const unsigned char *cursor = buffer;
    while (length) {
        if (write_abort_requested()) {
            errno = ECANCELED;
            return WRITE_STOPPED;
        }
        if (bounded_poll_timeout(deadline_ns) < 0) {
            request_stop(STOP_OUTPUT);
            return WRITE_ERROR;
        }

        ssize_t written = write(output_fd, cursor, length);
        if (written > 0) {
            cursor += written;
            length -= (size_t)written;
            if (length && parent_fd >= 0) {
                struct pollfd parent = {
                    .fd = parent_fd,
                    .events = POLLIN | POLLHUP
                };
                int polled = poll(&parent, 1, 0);
                if (polled > 0 &&
                    (parent.revents &
                     (POLLIN | POLLHUP | POLLERR | POLLNVAL))) {
                    int parent_status =
                        parent_event_status(parent_fd, parent.revents);
                    if (parent_status > 0) {
                        request_stop(STOP_STDIN_EOF);
                        errno = ECANCELED;
                        return WRITE_STOPPED;
                    }
                    if (parent_status < 0) {
                        request_stop(STOP_INPUT);
                        return WRITE_ERROR;
                    }
                } else if (polled < 0 && errno != EINTR) {
                    request_stop(STOP_INPUT);
                    return WRITE_ERROR;
                }
            }
            continue;
        }
        if (written < 0 && errno == EINTR)
            continue;
        if (written < 0 && errno != EAGAIN && errno != EWOULDBLOCK) {
            request_stop(STOP_OUTPUT);
            return WRITE_ERROR;
        }

        int timeout_ms = bounded_poll_timeout(deadline_ns);
        if (timeout_ms < 0) {
            request_stop(STOP_OUTPUT);
            return WRITE_ERROR;
        }
        struct pollfd descriptors[2] = {
            {.fd = output_fd, .events = POLLOUT},
            {.fd = parent_fd, .events = POLLIN | POLLHUP}
        };
        nfds_t descriptor_count = parent_fd >= 0 ? 2 : 1;
        int polled = poll(descriptors, descriptor_count, timeout_ms);
        if (polled < 0) {
            if (errno == EINTR)
                continue;
            request_stop(STOP_OUTPUT);
            return WRITE_ERROR;
        }
        if (polled == 0)
            continue;
        if (parent_fd >= 0 &&
            (descriptors[1].revents &
             (POLLIN | POLLHUP | POLLERR | POLLNVAL))) {
            int parent_status =
                parent_event_status(parent_fd, descriptors[1].revents);
            if (parent_status > 0) {
                request_stop(STOP_STDIN_EOF);
                errno = ECANCELED;
                return WRITE_STOPPED;
            }
            if (parent_status < 0) {
                request_stop(STOP_INPUT);
                return WRITE_ERROR;
            }
        }
        if (descriptors[0].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            errno = EPIPE;
            request_stop(STOP_OUTPUT);
            return WRITE_ERROR;
        }
    }
    return WRITE_OK;
}

static enum write_result write_frame_record(int fd, int parent_fd,
    uint64_t deadline_ns, const char *camera, uint32_t sequence,
    uint64_t receive_monotonic_ns, uint64_t timestamp_ns, uint32_t width,
    uint32_t height, const void *jpeg, size_t image_bytes)
{
    char header[512];
    if (image_bytes > UINT32_MAX) {
        errno = EOVERFLOW;
        return -1;
    }
    int header_length = snprintf(header, sizeof header,
        "{\"camera\":\"%s\",\"sequence\":%" PRIu32 ","
        "\"receive_monotonic_ns\":%" PRIu64 ","
        "\"timestamp_ns\":%" PRIu64 ","
        "\"timestamp_kind\":\"host_receive_not_exposure\","
        "\"encoding\":\"jpeg\",\"width\":%" PRIu32 ","
        "\"height\":%" PRIu32 ",\"image_bytes\":%zu}",
        camera, sequence, receive_monotonic_ns, timestamp_ns, width, height,
        image_bytes);
    if (header_length < 0 || (size_t)header_length >= sizeof header) {
        errno = EOVERFLOW;
        return -1;
    }
    uint32_t length = (uint32_t)header_length;
    unsigned char prefix[8] = {
        'O', 'A', 'C', '1',
        (unsigned char)(length >> 24),
        (unsigned char)(length >> 16),
        (unsigned char)(length >> 8),
        (unsigned char)length
    };
    enum write_result result = write_all_bounded(fd, parent_fd, prefix,
        sizeof prefix, deadline_ns);
    if (result != WRITE_OK)
        return result;
    result = write_all_bounded(fd, parent_fd, header, length, deadline_ns);
    if (result != WRITE_OK)
        return result;
    return write_all_bounded(fd, parent_fd, jpeg, image_bytes, deadline_ns);
}

static void report_bad_frame(struct camera *camera, const uvc_frame_t *frame,
    int complete, int dimensions_match, const char *error)
{
    pthread_mutex_lock(&output_mutex);
    fprintf(stderr,
        "{\"event\":\"frame\",\"camera\":\"%s\",\"sequence\":%" PRIu32 ","
        "\"decoded\":false,\"complete\":%s,\"dimensions_match\":%s,"
        "\"width\":%" PRIu32 ",\"height\":%" PRIu32 ",\"image_bytes\":%zu,"
        "\"error\":\"%s\"}\n",
        camera->target->name, frame->sequence, complete ? "true" : "false",
        dimensions_match ? "true" : "false", frame->width, frame->height,
        frame->data_bytes, error);
    fflush(stderr);
    pthread_mutex_unlock(&output_mutex);
}

static void receive_frame(uvc_frame_t *frame, void *user)
{
    struct camera *camera = user;
    if (stop_requested())
        return;
    struct timespec monotonic_received = {0};
    struct timespec realtime_received = {0};
    double received = 0.0;
    int clocks_ok = 1;
    if (camera->frames_mode) {
        clocks_ok = clock_gettime(CLOCK_MONOTONIC, &monotonic_received) == 0 &&
            clock_gettime(CLOCK_REALTIME, &realtime_received) == 0;
    } else {
        received = monotonic_seconds();
    }
    const unsigned char *jpeg = frame->data;
    int complete = frame->data_bytes >= 4 &&
        jpeg[0] == 0xff && jpeg[1] == 0xd8 &&
        jpeg[frame->data_bytes - 2] == 0xff &&
        jpeg[frame->data_bytes - 1] == 0xd9;
    int dimensions_match =
        frame->width == camera->width && frame->height == camera->height;
    int decoded = clocks_ok && complete && dimensions_match &&
        uvc_mjpeg2gray(frame, camera->decoded) == UVC_SUCCESS;
    camera->frames++;
    if (!decoded) {
        camera->bad++;
        if (camera->frames_mode) {
            const char *error = !clocks_ok ? "receipt_clock_failed" :
                !complete ? "incomplete_jpeg" :
                !dimensions_match ? "frame_dimensions_mismatch" :
                "jpeg_decode_failed";
            request_stop(STOP_BAD_FRAME);
            report_bad_frame(camera, frame, complete, dimensions_match, error);
            return;
        }
    }
    if (camera->frames_mode) {
        pthread_mutex_lock(&output_mutex);
        if (stop_requested()) {
            pthread_mutex_unlock(&output_mutex);
            return;
        }
        uint64_t write_started_ns;
        enum write_result write_result = WRITE_ERROR;
        if (!monotonic_ns(&write_started_ns)) {
            write_result = write_frame_record(STDOUT_FILENO, STDIN_FILENO,
                write_started_ns + FRAME_WRITE_TIMEOUT_NS, camera->target->name,
                frame->sequence, timespec_ns(&monotonic_received),
                timespec_ns(&realtime_received), frame->width, frame->height,
                frame->data, frame->data_bytes);
        }
        if (write_result == WRITE_ERROR) {
            int error_number = errno;
            request_stop(STOP_OUTPUT);
            fprintf(stderr,
                "{\"event\":\"error\",\"camera\":\"%s\","
                "\"error\":\"stdout_write_failed\",\"errno\":%d}\n",
                camera->target->name, error_number);
            fflush(stderr);
        }
        pthread_mutex_unlock(&output_mutex);
        return;
    }
    /* Metadata mode never writes encoded frames or decoded pixels. */
    pthread_mutex_lock(&output_mutex);
    printf("{\"event\":\"frame\",\"camera\":\"%s\","
           "\"monotonic_s\":%.9f,\"sequence\":%u,\"bytes\":%zu,"
           "\"decoded\":%s,\"width\":%u,\"height\":%u}\n",
           camera->target->name, received, frame->sequence, frame->data_bytes,
           decoded ? "true" : "false", frame->width, frame->height);
    pthread_mutex_unlock(&output_mutex);
}

static int parse_unsigned(const char *text, unsigned *value)
{
    char *end = NULL;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 10);
    if (errno || !text[0] || *end || parsed > 100000)
        return -1;
    *value = (unsigned)parsed;
    return 0;
}

static const struct camera_target *find_camera_target(const char *text,
    size_t length)
{
    for (size_t index = 0;
         index < sizeof camera_targets / sizeof camera_targets[0]; index++) {
        const struct camera_target *target = &camera_targets[index];
        if (strlen(target->port_path) == length &&
            !memcmp(target->port_path, text, length))
            return target;
    }
    return NULL;
}

static int parse_ports(const char *list,
    const struct camera_target *selected[MAX_CAMERAS], size_t *selected_count)
{
    size_t count = 0;
    const char *cursor = list;
    if (!cursor || !*cursor)
        return -1;
    while (*cursor) {
        const char *separator = strchr(cursor, ',');
        size_t length = separator ? (size_t)(separator - cursor) : strlen(cursor);
        if (!length || count == MAX_CAMERAS)
            return -1;
        const struct camera_target *target = find_camera_target(cursor, length);
        if (!target)
            return -1;
        for (size_t index = 0; index < count; index++) {
            if (selected[index] == target)
                return -1;
        }
        selected[count++] = target;
        if (!separator)
            break;
        cursor = separator + 1;
        if (!*cursor)
            return -1;
    }
    *selected_count = count;
    return 0;
}

static void default_ports(const struct camera_target *selected[MAX_CAMERAS],
    size_t *selected_count)
{
    selected[0] = &camera_targets[1];
    selected[1] = &camera_targets[2];
    *selected_count = 2;
}

static int device_matches_target(libusb_device *device,
    const struct camera_target *target)
{
    struct libusb_device_descriptor descriptor;
    uint8_t ports[8];
    if (libusb_get_device_descriptor(device, &descriptor) ||
        descriptor.idVendor != 0x2bcf || descriptor.idProduct != 0x1258 ||
        libusb_get_bus_number(device) != 3)
        return 0;
    int depth = libusb_get_port_numbers(device, ports, sizeof ports);
    return depth == (int)target->port_count &&
        !memcmp(ports, target->ports, target->port_count);
}

static int address_matches_target(uint8_t address,
    const struct camera_target *target)
{
    libusb_context *context = NULL;
    libusb_device **devices = NULL;
    if (libusb_init(&context) < 0)
        return 0;
    ssize_t count = libusb_get_device_list(context, &devices);
    if (count < 0) {
        libusb_exit(context);
        return 0;
    }
    unsigned matches = 0;
    for (ssize_t index = 0; index < count; index++) {
        if (libusb_get_bus_number(devices[index]) == 3 &&
            libusb_get_device_address(devices[index]) == address &&
            device_matches_target(devices[index], target))
            matches++;
    }
    libusb_free_device_list(devices, 1);
    libusb_exit(context);
    return matches == 1;
}

static int camera_addresses(struct camera cameras[MAX_CAMERAS],
    size_t camera_count)
{
    libusb_context *context = NULL;
    libusb_device **devices = NULL;
    if (libusb_init(&context) < 0)
        return -1;
    ssize_t count = libusb_get_device_list(context, &devices);
    if (count < 0) {
        libusb_exit(context);
        return -1;
    }
    unsigned matches[MAX_CAMERAS] = {0};
    for (ssize_t index = 0; index < count; index++) {
        for (size_t camera_index = 0; camera_index < camera_count; camera_index++) {
            struct camera *camera = &cameras[camera_index];
            if (!device_matches_target(devices[index], camera->target))
                continue;
            matches[camera_index]++;
            camera->address = libusb_get_device_address(devices[index]);
        }
    }
    libusb_free_device_list(devices, 1);
    libusb_exit(context);
    for (size_t index = 0; index < camera_count; index++) {
        if (matches[index] != 1 || !cameras[index].address)
            return -1;
    }
    return 0;
}

static void close_camera(struct camera *camera)
{
    if (camera->stream) {
        uvc_stream_close(camera->stream);
        camera->stream = NULL;
    }
    if (camera->handle) {
        if (camera->saved_controls || camera->changed_controls)
            camera->controls_restore_failed = restore_controls(camera);
        uvc_close(camera->handle);
        camera->handle = NULL;
    }
    if (camera->decoded) {
        uvc_free_frame(camera->decoded);
        camera->decoded = NULL;
    }
}

static int read_hex_attribute(const char *path, unsigned *value)
{
    FILE *attribute = fopen(path, "r");
    if (!attribute)
        return -1;
    int scanned = fscanf(attribute, "%x", value);
    int closed = fclose(attribute);
    return scanned == 1 && !closed ? 0 : -1;
}

static int interface_driver(const struct camera_target *target, int interface,
    char *name, size_t name_size)
{
    char path[192], link_target[256];
    snprintf(path, sizeof path, "/sys/bus/usb/devices/%s:1.%d/driver",
        target->sysfs_path, interface);
    errno = 0;
    ssize_t length = readlink(path, link_target, sizeof link_target - 1);
    if (length < 0)
        return errno == ENOENT ? 0 : -1;
    link_target[length] = '\0';
    const char *basename = strrchr(link_target, '/');
    basename = basename ? basename + 1 : link_target;
    int written = snprintf(name, name_size, "%s", basename);
    return written >= 0 && (size_t)written < name_size ? 1 : -1;
}

static int bound_to_uvc(const struct camera_target *target, int interface)
{
    char driver[64];
    return interface_driver(target, interface, driver, sizeof driver) == 1 &&
        !strcmp(driver, "uvcvideo");
}

static int validate_uvc_interface(const struct camera_target *target,
    int interface, unsigned expected_subclass)
{
    char path[192];
    unsigned interface_class = 0, interface_subclass = 0;
    snprintf(path, sizeof path, "/sys/bus/usb/devices/%s:1.%d/bInterfaceClass",
        target->sysfs_path, interface);
    if (read_hex_attribute(path, &interface_class))
        return -1;
    snprintf(path, sizeof path, "/sys/bus/usb/devices/%s:1.%d/bInterfaceSubClass",
        target->sysfs_path, interface);
    if (read_hex_attribute(path, &interface_subclass))
        return -1;
    return interface_class == 0x0e &&
        interface_subclass == expected_subclass ? 0 : -1;
}

static int restore_binding(const struct camera_target *target)
{
    char path[192];
    unsigned expected[2] = {0x2bcf, 0x1258};
    const char *attributes[2] = {"idVendor", "idProduct"};
    for (int index = 0; index < 2; index++) {
        snprintf(path, sizeof path, "/sys/bus/usb/devices/%s/%s",
            target->sysfs_path, attributes[index]);
        unsigned actual = 0;
        if (read_hex_attribute(path, &actual) || actual != expected[index])
            return -1;
    }
    if (validate_uvc_interface(target, 0, 1) || validate_uvc_interface(target, 1, 2))
        return -1;
    for (int interface = 0; interface <= 1; interface++) {
        char driver[64];
        int driver_status = interface_driver(target, interface, driver, sizeof driver);
        if (driver_status < 0)
            return -1;
        if (driver_status == 1) {
            if (strcmp(driver, "uvcvideo"))
                return -1;
            continue;
        }
        /* Recovery only binds an unclaimed selected camera interface. */
        FILE *bind = fopen("/sys/bus/usb/drivers/uvcvideo/bind", "w");
        if (!bind)
            return -1;
        int written = fprintf(bind, "%s:1.%d", target->sysfs_path, interface);
        int closed = fclose(bind);
        if (written < 0 || closed)
            return -1;
        for (int attempt = 0; attempt < 20; attempt++) {
            if (bound_to_uvc(target, interface))
                break;
            struct timespec pause = {.tv_nsec = 50000000};
            nanosleep(&pause, NULL);
        }
        if (!bound_to_uvc(target, interface))
            return -1;
    }
    return bound_to_uvc(target, 0) && bound_to_uvc(target, 1) ? 0 : -1;
}

#ifndef UVC_PAIR_PROBE_UNIT_TEST
static void event_stream(FILE *stream, const char *event, const char *camera,
    double monotonic_s, unsigned frames, unsigned bad)
{
    pthread_mutex_lock(&output_mutex);
    if (!strcmp(event, "start")) {
        fprintf(stream,
            "{\"event\":\"start\",\"camera\":\"%s\","
            "\"monotonic_s\":%.9f}\n", camera, monotonic_s);
    } else {
        fprintf(stream,
            "{\"event\":\"end\",\"camera\":\"%s\","
            "\"monotonic_s\":%.9f,\"frames\":%u,\"bad\":%u}\n",
            camera, monotonic_s, frames, bad);
    }
    fflush(stream);
    pthread_mutex_unlock(&output_mutex);
}

static int poll_parent_stdin(int timeout_ms)
{
    struct pollfd input = {.fd = STDIN_FILENO, .events = POLLIN | POLLHUP};
    int polled = poll(&input, 1, timeout_ms);
    if (polled < 0) {
        if (errno == EINTR)
            return 0;
        int error_number = errno;
        locked_fprintf(stderr,
            "{\"event\":\"error\",\"error\":\"stdin_poll_failed\","
            "\"errno\":%d}\n", error_number);
        request_stop(STOP_INPUT);
        return -1;
    }
    if (polled == 0 ||
        !(input.revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL)))
        return 0;

    int parent_status = parent_event_status(STDIN_FILENO, input.revents);
    if (parent_status > 0) {
        request_stop(STOP_STDIN_EOF);
        return 1;
    }
    if (parent_status < 0) {
        int error_number = errno;
        locked_fprintf(stderr,
            "{\"event\":\"error\",\"error\":\"stdin_read_failed\","
            "\"errno\":%d}\n", error_number);
        request_stop(STOP_INPUT);
        return -1;
    }
    return 0;
}

static void wait_for_stop(double deadline, int frames_mode)
{
    while (!stop_requested() && monotonic_seconds() < deadline) {
        if (!frames_mode) {
            struct timespec pause = {.tv_nsec = 100000000};
            nanosleep(&pause, NULL);
            continue;
        }
        int timeout_ms = 100;
        double remaining = deadline - monotonic_seconds();
        if (remaining <= 0)
            break;
        if (remaining < 0.1)
            timeout_ms = (int)(remaining * 1000.0);
        if (poll_parent_stdin(timeout_ms))
            break;
    }
}

static void print_usage(const char *program)
{
    fprintf(stderr,
        "Usage: %s SECONDS WIDTH HEIGHT PAYLOAD [--frames] [--ports LIST]\n"
        "       [--fps 15|20|25|30] [--manual-exposure 100US] [--gain 0..255]\n"
        "       [--ae-priority 0|1] (temporary controls restored on clean exit)\n"
        "       %s --restore-only [--ports LIST]\n"
        "Default targets: bus 3, ports 2.2 and 2.3, VID:PID 2bcf:1258.\n"
        "LIST selects 1..4 distinct ports from: 1,2.2,2.3,2.4.\n"
        "PAYLOAD=0 keeps the camera-reported value. Metadata mode "
        "allows <=90s; --frames allows <=10800s.\n", program, program);
}

int main(int argc, char **argv)
{
    const struct camera_target *selected[MAX_CAMERAS] = {0};
    size_t camera_count = 0;
    default_ports(selected, &camera_count);
    int restore_only = argc >= 2 && !strcmp(argv[1], "--restore-only");
    int frames_mode = 0, ports_set = 0;
    struct stream_options options = {.fps = 30, .exposure = -1, .gain = -1, .ae_priority = -1};
    unsigned seconds = 0, width = 0, height = 0, payload = 0;
    int invalid = !restore_only && (argc < 5 ||
        parse_unsigned(argv[1], &seconds) || parse_unsigned(argv[2], &width) ||
        parse_unsigned(argv[3], &height) || parse_unsigned(argv[4], &payload));
    for (int index = restore_only ? 2 : 5; !invalid && index < argc; index++) {
        const char *option = argv[index];
        if (!strcmp(option, "--frames") && !restore_only && !frames_mode) {
            frames_mode = 1;
            continue;
        }
        if (index + 1 >= argc) {
            invalid = 1;
            break;
        }
        const char *value = argv[++index];
        if (!strcmp(option, "--ports") && !ports_set) {
            invalid = parse_ports(value, selected, &camera_count) != 0;
            ports_set = 1;
            continue;
        }
        unsigned parsed = 0;
        if (restore_only || parse_unsigned(value, &parsed)) {
            invalid = 1;
            break;
        }
        if (!strcmp(option, "--fps") &&
            (parsed == 15 || parsed == 20 || parsed == 25 || parsed == 30))
            options.fps = parsed;
        else if (!strcmp(option, "--manual-exposure") && parsed >= 3 && parsed <= 2047)
            options.exposure = (int)parsed;
        else if (!strcmp(option, "--gain") && parsed <= 255)
            options.gain = (int)parsed;
        else if (!strcmp(option, "--ae-priority") && parsed <= 1)
            options.ae_priority = (int)parsed;
        else
            invalid = 1;
    }
    if (invalid || (!restore_only &&
        (seconds < 1 || seconds > (frames_mode ? 10800U : 90U) ||
         !((width == 640 && height == 480) || (width == 1280 && height == 720) ||
           (width == 1920 && height == 1080)) ||
         !(payload == 0 || payload == 800 || payload == 1280 || payload == 1600 ||
           payload == 1984 || payload == 3060) ||
         (options.exposure >= 0 && (unsigned)options.exposure > 10000U / options.fps)))) {
        print_usage(argv[0]);
        return 2;
    }
    if (restore_only) {
        int failed = 0;
        for (size_t index = 0; index < camera_count; index++) {
            if (restore_binding(selected[index])) {
                locked_fprintf(stderr, "port %s: UVC restoration failed\n",
                    selected[index]->port_path);
                failed = 1;
            }
        }
        return failed;
    }
    setvbuf(stdout, NULL, frames_mode ? _IONBF : _IOLBF, 0);
    setvbuf(stderr, NULL, _IOLBF, 0);
    signal(SIGINT, stop_signal);
    signal(SIGTERM, stop_signal);
    signal(SIGHUP, stop_signal);
    signal(SIGPIPE, stop_signal);
    int stdout_flags = -1;
    int stdin_flags = -1;
    if (frames_mode) {
        if (set_nonblocking(STDOUT_FILENO, &stdout_flags)) {
            locked_fprintf(stderr,
                "{\"event\":\"error\","
                "\"error\":\"stdout_nonblocking_failed\",\"errno\":%d}\n",
                errno);
            return 1;
        }
        if (set_nonblocking(STDIN_FILENO, &stdin_flags)) {
            int error_number = errno;
            restore_flags(STDOUT_FILENO, stdout_flags);
            if (error_number == EBADF)
                return 0;
            locked_fprintf(stderr,
                "{\"event\":\"error\","
                "\"error\":\"stdin_nonblocking_failed\",\"errno\":%d}\n",
                error_number);
            return 1;
        }
        int parent_status = poll_parent_stdin(0);
        if (parent_status) {
            restore_flags(STDIN_FILENO, stdin_flags);
            restore_flags(STDOUT_FILENO, stdout_flags);
            return parent_status < 0 ? 1 : 0;
        }
    }

    struct camera cameras[MAX_CAMERAS] = {0};
    for (size_t index = 0; index < camera_count; index++) {
        cameras[index].target = selected[index];
        cameras[index].width = width;
        cameras[index].height = height;
        cameras[index].frames_mode = frames_mode;
    }
    if (camera_addresses(cameras, camera_count)) {
        locked_fprintf(stderr, "Expected cameras not found on bus 3 at all selected ports\n");
        restore_flags(STDIN_FILENO, stdin_flags);
        restore_flags(STDOUT_FILENO, stdout_flags);
        return 1;
    }
    uvc_context_t *context = NULL;
    uvc_device_t **devices = NULL;
    int result = 1;
    int status = uvc_init(&context, NULL);
    if (status < 0) {
        locked_fprintf(stderr, "uvc_init: %s\n", uvc_strerror(status));
        restore_flags(STDIN_FILENO, stdin_flags);
        restore_flags(STDOUT_FILENO, stdout_flags);
        return 1;
    }
    status = uvc_get_device_list(context, &devices);
    if (status < 0) {
        locked_fprintf(stderr, "uvc_get_device_list: %s\n",
            uvc_strerror(status));
        goto cleanup;
    }

    for (size_t device_index = 0; devices[device_index]; device_index++) {
        uvc_device_t *device = devices[device_index];
        if (uvc_get_bus_number(device) != 3)
            continue;
        struct camera *camera = NULL;
        uint8_t address = uvc_get_device_address(device);
        for (size_t camera_index = 0; camera_index < camera_count; camera_index++) {
            if (address == cameras[camera_index].address) {
                camera = &cameras[camera_index];
                break;
            }
        }
        if (!camera)
            continue;
        uvc_device_descriptor_t *descriptor = NULL;
        status = uvc_get_device_descriptor(device, &descriptor);
        if (status < 0)
            continue;
        int descriptor_matches = descriptor->idVendor == 0x2bcf &&
            descriptor->idProduct == 0x1258;
        uvc_free_device_descriptor(descriptor);
        /* Check the exact port vector before uvc_open can detach an interface. */
        if (!descriptor_matches ||
            !address_matches_target(camera->address, camera->target)) {
            locked_fprintf(stderr, "%s topology changed before open; refusing device\n",
                camera->target->name);
            goto cleanup;
        }
        if (camera->handle) {
            locked_fprintf(stderr, "%s appeared more than once\n", camera->target->name);
            goto cleanup;
        }
        camera->touched = 1;
        status = uvc_open(device, &camera->handle);
        if (status < 0) {
            locked_fprintf(stderr, "%s open: %s\n", camera->target->name, uvc_strerror(status));
            goto cleanup;
        }
    }

    for (size_t index = 0; index < camera_count; index++) {
        if (stop_requested()) {
            result = stopped_result();
            goto cleanup;
        }
        struct camera *camera = &cameras[index];
        if (!camera->handle) {
            locked_fprintf(stderr, "%s not found at the expected address\n",
                camera->target->name);
            goto cleanup;
        }
        if (apply_controls(camera, &options)) {
            locked_fprintf(stderr, "%s control configuration/readback failed\n", camera->target->name);
            goto cleanup;
        }
        camera->decoded = uvc_allocate_frame((size_t)width * height);
        if (!camera->decoded)
            goto cleanup;
        uvc_stream_ctrl_t control = {0};
        status = uvc_get_stream_ctrl_format_size(camera->handle, &control,
            UVC_FRAME_FORMAT_MJPEG, (int)width, (int)height, (int)options.fps);
        if (status < 0) {
            locked_fprintf(stderr, "%s negotiate: %s\n", camera->target->name,
                uvc_strerror(status));
            goto cleanup;
        }
        if (frames_mode && poll_parent_stdin(0)) {
            result = stopped_result();
            goto cleanup;
        }
        FILE *diagnostics = frames_mode ? stderr : stdout;
        pthread_mutex_lock(&output_mutex);
        fprintf(diagnostics,
                "{\"event\":\"negotiated\",\"camera\":\"%s\","
                "\"original_payload\":%u,\"selected_payload\":%u,"
                "\"frame_interval_100ns\":%u,\"frame_buffer_bytes\":%u}\n",
                camera->target->name, control.dwMaxPayloadTransferSize,
                payload ? payload : control.dwMaxPayloadTransferSize,
                control.dwFrameInterval, control.dwMaxVideoFrameSize);
        fflush(diagnostics);
        pthread_mutex_unlock(&output_mutex);
        if (frames_mode && stop_requested()) {
            result = stopped_result();
            goto cleanup;
        }
        if (payload)
            control.dwMaxPayloadTransferSize = payload;
        status = uvc_stream_open_ctrl(camera->handle, &camera->stream, &control);
        if (status < 0) {
            locked_fprintf(stderr, "%s stream open: %s\n", camera->target->name,
                uvc_strerror(status));
            goto cleanup;
        }
        if (frames_mode && stop_requested()) {
            result = stopped_result();
            goto cleanup;
        }
        event_stream(frames_mode ? stderr : stdout, "start", camera->target->name,
            monotonic_seconds(), 0, 0);
        if (frames_mode && stop_requested()) {
            result = stopped_result();
            goto cleanup;
        }
        status = uvc_stream_start(camera->stream, receive_frame, camera, 0);
        if (status < 0) {
            locked_fprintf(stderr, "%s stream start: %s\n", camera->target->name,
                uvc_strerror(status));
            goto cleanup;
        }
        if (frames_mode && poll_parent_stdin(0)) {
            result = stopped_result();
            goto cleanup;
        }
    }

    double deadline = monotonic_seconds() + seconds;
    wait_for_stop(deadline, frames_mode);
    result = stop_requested() ? stopped_result() : 0;

cleanup:
    /* Stop every stream before USB control restoration so other streams are not
       held up by restoration queries while still publishing frames. */
    for (size_t index = 0; index < camera_count; index++) {
        if (cameras[index].stream) {
            uvc_stream_close(cameras[index].stream);
            cameras[index].stream = NULL;
        }
    }
    for (size_t index = 0; index < camera_count; index++) {
        struct camera *camera = &cameras[index];
        close_camera(camera);
        event_stream(frames_mode ? stderr : stdout, "end", camera->target->name,
            monotonic_seconds(), camera->frames, camera->bad);
        if (camera->bad || camera->controls_restore_failed ||
            (!camera->frames &&
             current_stop_reason() != STOP_STDIN_EOF))
            result = result ? result : 1;
    }
    if (devices)
        uvc_free_device_list(devices, 1);
    uvc_exit(context);
    for (size_t index = 0; index < camera_count; index++) {
        if (cameras[index].touched && restore_binding(cameras[index].target)) {
            locked_fprintf(stderr, "%s: UVC restoration failed\n",
                cameras[index].target->name);
            result = result ? result : 1;
        }
    }
    restore_flags(STDIN_FILENO, stdin_flags);
    restore_flags(STDOUT_FILENO, stdout_flags);
    return result;
}
#endif
