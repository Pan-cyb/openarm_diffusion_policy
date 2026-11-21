#define UVC_PAIR_PROBE_UNIT_TEST
#include "uvc_pair_probe.c"

static int fail(const char *message)
{
    fprintf(stderr, "offline framing test failed: %s\n", message);
    return 1;
}

static void reset_stop_state(void)
{
    atomic_store_explicit(&stop_reason, STOP_NONE, memory_order_relaxed);
    atomic_store_explicit(&stopping, 0, memory_order_release);
}

static int deadline_after(uint64_t duration_ns, uint64_t *deadline_ns)
{
    uint64_t now_ns;
    if (monotonic_ns(&now_ns))
        return -1;
    *deadline_ns = now_ns + duration_ns;
    return 0;
}

static int test_byte_layout(void)
{
    /* This is a mock marker fixture, not a real or decodable JPEG. */
    static const unsigned char mock_jpeg[] = {
        0xff, 0xd8, 'M', 'O', 'C', 'K', 0xff, 0xd9
    };
    static const char expected_header[] =
        "{\"camera\":\"wrist_port_2_2\",\"sequence\":7,"
        "\"receive_monotonic_ns\":123456789,"
        "\"timestamp_ns\":1773630000123456789,"
        "\"timestamp_kind\":\"host_receive_not_exposure\","
        "\"encoding\":\"jpeg\",\"width\":640,\"height\":480,\"image_bytes\":8}";
    int pipe_fds[2];
    if (pipe(pipe_fds))
        return fail("pipe");
    int output_flags = -1;
    uint64_t deadline_ns;
    if (set_nonblocking(pipe_fds[1], &output_flags) ||
        deadline_after(FRAME_WRITE_TIMEOUT_NS, &deadline_ns)) {
        close(pipe_fds[0]);
        close(pipe_fds[1]);
        return fail("layout setup");
    }
    reset_stop_state();
    if (write_frame_record(pipe_fds[1], -1, deadline_ns,
            "wrist_port_2_2", 7, 123456789,
            UINT64_C(1773630000123456789), 640, 480, mock_jpeg,
            sizeof mock_jpeg) != WRITE_OK) {
        close(pipe_fds[0]);
        close(pipe_fds[1]);
        return fail("writer");
    }
    close(pipe_fds[1]);

    unsigned char output[1024];
    ssize_t output_bytes = read(pipe_fds[0], output, sizeof output);
    close(pipe_fds[0]);
    if (output_bytes < 0)
        return fail("read");
    size_t expected_length = strlen(expected_header);
    size_t expected_total = 8 + expected_length + sizeof mock_jpeg;
    if ((size_t)output_bytes != expected_total)
        return fail("record length");
    if (memcmp(output, "OAC1", 4))
        return fail("magic");
    uint32_t header_length = ((uint32_t)output[4] << 24) |
        ((uint32_t)output[5] << 16) | ((uint32_t)output[6] << 8) | output[7];
    if (header_length != expected_length)
        return fail("big-endian header length");
    if (memcmp(output + 8, expected_header, expected_length))
        return fail("JSON header bytes");
    if (memcmp(output + 8 + expected_length, mock_jpeg, sizeof mock_jpeg))
        return fail("mock image bytes");

    puts("offline framing layout test passed (mock marker fixture)");
    return 0;
}

static int fill_pipe(int fd, size_t *filled_bytes)
{
    unsigned char fill[4096];
    *filled_bytes = 0;
    memset(fill, 0xa5, sizeof fill);
    for (;;) {
        ssize_t written = write(fd, fill, sizeof fill);
        if (written > 0) {
            *filled_bytes += (size_t)written;
            continue;
        }
        if (written < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
            return 0;
        if (written < 0 && errno == EINTR)
            continue;
        return -1;
    }
}

static int test_full_pipe_parent_eof(void)
{
    static const unsigned char mock_jpeg[] = {
        0xff, 0xd8, 'M', 'O', 'C', 'K', 0xff, 0xd9
    };
    int output_pipe[2];
    int parent_pipe[2];
    if (pipe(output_pipe) || pipe(parent_pipe))
        return fail("full-pipe setup");
    int output_flags = -1;
    int parent_flags = -1;
    size_t filled_bytes;
    if (set_nonblocking(output_pipe[1], &output_flags) ||
        set_nonblocking(parent_pipe[0], &parent_flags) ||
        fill_pipe(output_pipe[1], &filled_bytes)) {
        return fail("full-pipe fill");
    }
    (void)filled_bytes;
    close(parent_pipe[1]);
    reset_stop_state();
    uint64_t started_ns;
    uint64_t deadline_ns;
    if (monotonic_ns(&started_ns) ||
        deadline_after(FRAME_WRITE_TIMEOUT_NS, &deadline_ns))
        return fail("full-pipe clock");
    enum write_result result = write_frame_record(output_pipe[1],
        parent_pipe[0], deadline_ns, "wrist_port_2_2", 8, 123456790,
        UINT64_C(1773630000123456790), 640, 480, mock_jpeg,
        sizeof mock_jpeg);
    uint64_t finished_ns;
    if (monotonic_ns(&finished_ns))
        return fail("full-pipe finish clock");
    close(output_pipe[0]);
    close(output_pipe[1]);
    close(parent_pipe[0]);
    if (result != WRITE_STOPPED)
        return fail("full pipe did not stop on parent EOF");
    if (!stop_requested() || current_stop_reason() != STOP_STDIN_EOF)
        return fail("parent EOF stop state");
    if (stopped_result() != 0 || errno != ECANCELED)
        return fail("parent EOF graceful exit semantics");
    if (finished_ns - started_ns > UINT64_C(500000000))
        return fail("parent EOF bounded return");

    puts("offline full-pipe parent-EOF test passed");
    return 0;
}

static int test_epipe(void)
{
    static const unsigned char mock_jpeg[] = {
        0xff, 0xd8, 'M', 'O', 'C', 'K', 0xff, 0xd9
    };
    int output_pipe[2];
    if (pipe(output_pipe))
        return fail("EPIPE setup");
    int output_flags = -1;
    if (set_nonblocking(output_pipe[1], &output_flags))
        return fail("EPIPE nonblocking");
    close(output_pipe[0]);
    reset_stop_state();
    signal(SIGPIPE, stop_signal);
    uint64_t started_ns;
    uint64_t deadline_ns;
    if (monotonic_ns(&started_ns) ||
        deadline_after(FRAME_WRITE_TIMEOUT_NS, &deadline_ns))
        return fail("EPIPE clock");
    enum write_result result = write_frame_record(output_pipe[1], -1,
        deadline_ns, "wrist_port_2_3", 9, 123456791,
        UINT64_C(1773630000123456791), 640, 480, mock_jpeg,
        sizeof mock_jpeg);
    int error_number = errno;
    uint64_t finished_ns;
    if (monotonic_ns(&finished_ns))
        return fail("EPIPE finish clock");
    close(output_pipe[1]);
    if (result != WRITE_ERROR || error_number != EPIPE)
        return fail("EPIPE result");
    if (!stop_requested() || current_stop_reason() != STOP_OUTPUT)
        return fail("EPIPE stop state");
    if (stopped_result() != 1)
        return fail("EPIPE exit semantics");
    if (finished_ns - started_ns > UINT64_C(500000000))
        return fail("EPIPE bounded return");

    puts("offline EPIPE test passed");
    return 0;
}

static int test_bad_frame_precedence(void)
{
    reset_stop_state();
    request_stop(STOP_BAD_FRAME);
    request_stop(STOP_STDIN_EOF);
    if (current_stop_reason() != STOP_BAD_FRAME || stopped_result() != 1)
        return fail("bad frame precedence over later parent EOF");

    puts("offline bad-frame precedence test passed");
    return 0;
}

static int test_full_pipe_timeout(void)
{
    static const unsigned char mock_jpeg[] = {
        0xff, 0xd8, 'M', 'O', 'C', 'K', 0xff, 0xd9
    };
    int output_pipe[2];
    int parent_pipe[2];
    if (pipe(output_pipe) || pipe(parent_pipe))
        return fail("timeout setup");
    int output_flags = -1;
    int parent_flags = -1;
    size_t filled_bytes;
    if (set_nonblocking(output_pipe[1], &output_flags) ||
        set_nonblocking(parent_pipe[0], &parent_flags) ||
        fill_pipe(output_pipe[1], &filled_bytes))
        return fail("timeout fill");
    (void)filled_bytes;
    reset_stop_state();
    uint64_t started_ns;
    if (monotonic_ns(&started_ns))
        return fail("timeout clock");
    enum write_result result = write_frame_record(output_pipe[1],
        parent_pipe[0], started_ns + UINT64_C(100000000),
        "wrist_port_2_2", 10, 123456792,
        UINT64_C(1773630000123456792), 640, 480, mock_jpeg,
        sizeof mock_jpeg);
    int error_number = errno;
    uint64_t finished_ns;
    if (monotonic_ns(&finished_ns))
        return fail("timeout finish clock");
    close(output_pipe[0]);
    close(output_pipe[1]);
    close(parent_pipe[0]);
    close(parent_pipe[1]);
    if (result != WRITE_ERROR || error_number != ETIMEDOUT)
        return fail("full pipe timeout result");
    if (!stop_requested() || current_stop_reason() != STOP_OUTPUT)
        return fail("full pipe timeout stop state");
    if (finished_ns - started_ns > UINT64_C(500000000))
        return fail("full pipe timeout bound");

    puts("offline full-pipe timeout test passed");
    return 0;
}

struct reader_context {
    int fd;
    unsigned char *buffer;
    size_t expected_bytes;
    size_t received_bytes;
    int failed;
};

static void *delayed_reader(void *user)
{
    struct reader_context *reader = user;
    struct timespec delay = {.tv_nsec = 20000000};
    nanosleep(&delay, NULL);
    while (reader->received_bytes < reader->expected_bytes) {
        ssize_t bytes = read(reader->fd,
            reader->buffer + reader->received_bytes,
            reader->expected_bytes - reader->received_bytes);
        if (bytes > 0) {
            reader->received_bytes += (size_t)bytes;
            continue;
        }
        if (bytes < 0 && errno == EINTR)
            continue;
        reader->failed = 1;
        break;
    }
    return NULL;
}

static int test_partial_write_continuation(void)
{
    const size_t payload_bytes = 256 * 1024;
    unsigned char *payload = malloc(payload_bytes);
    if (!payload)
        return fail("partial payload allocation");
    for (size_t index = 0; index < payload_bytes; index++)
        payload[index] = (unsigned char)(index * 37U + 11U);

    int output_pipe[2];
    if (pipe(output_pipe)) {
        free(payload);
        return fail("partial pipe");
    }
    int output_flags = -1;
    size_t filled_bytes;
    if (set_nonblocking(output_pipe[1], &output_flags) ||
        fill_pipe(output_pipe[1], &filled_bytes)) {
        free(payload);
        return fail("partial pipe fill");
    }
    unsigned char *received = malloc(filled_bytes + payload_bytes);
    if (!received) {
        free(payload);
        return fail("partial receive allocation");
    }
    struct reader_context reader = {
        .fd = output_pipe[0],
        .buffer = received,
        .expected_bytes = filled_bytes + payload_bytes
    };
    pthread_t reader_thread;
    if (pthread_create(&reader_thread, NULL, delayed_reader, &reader)) {
        free(received);
        free(payload);
        return fail("partial reader thread");
    }

    reset_stop_state();
    uint64_t deadline_ns;
    if (deadline_after(FRAME_WRITE_TIMEOUT_NS, &deadline_ns)) {
        close(output_pipe[1]);
        pthread_join(reader_thread, NULL);
        free(received);
        free(payload);
        return fail("partial deadline");
    }
    enum write_result result = write_all_bounded(output_pipe[1], -1,
        payload, payload_bytes, deadline_ns);
    close(output_pipe[1]);
    pthread_join(reader_thread, NULL);
    close(output_pipe[0]);

    int valid = result == WRITE_OK && !reader.failed &&
        reader.received_bytes == reader.expected_bytes;
    for (size_t index = 0; valid && index < filled_bytes; index++)
        valid = received[index] == 0xa5;
    if (valid)
        valid = !memcmp(received + filled_bytes, payload, payload_bytes);
    free(received);
    free(payload);
    if (!valid)
        return fail("partial write continuation bytes");

    puts("offline partial-write continuation test passed (mock payload)");
    return 0;
}

int main(void)
{
    if (test_byte_layout() ||
        test_full_pipe_parent_eof() ||
        test_epipe() ||
        test_bad_frame_precedence() ||
        test_full_pipe_timeout() ||
        test_partial_write_continuation())
        return 1;
    return 0;
}
