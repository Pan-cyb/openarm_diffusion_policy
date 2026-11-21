#define _POSIX_C_SOURCE 200809L
#define _XOPEN_SOURCE 700
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <time.h>
#include <linux/videodev2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

static int attribute_matches(const char *port, const char *name, unsigned expected)
{
    char path[256];
    snprintf(path, sizeof path, "/sys/bus/usb/devices/3-%s/%s", port, name);
    FILE *file = fopen(path, "r");
    unsigned value = 0;
    if (!file)
        return 0;
    int ok = fscanf(file, "%x", &value) == 1 && value == expected;
    fclose(file);
    return ok;
}

static void string(const unsigned char *text)
{
    putchar('"');
    for (; *text; text++) {
        if (*text == '"' || *text == '\\')
            putchar('\\');
        if (*text >= 32)
            putchar(*text);
    }
    putchar('"');
}

int main(int argc, char **argv)
{
    if (argc != 2 || (strcmp(argv[1], "1") && strcmp(argv[1], "2.2") &&
                     strcmp(argv[1], "2.3") && strcmp(argv[1], "2.4"))) {
        fprintf(stderr, "Usage: %s PORT; read-only, bus3 ports 1,2.2,2.3,2.4 only\n", argv[0]);
        return 2;
    }
    const char *port = argv[1];
    if (!attribute_matches(port, "idVendor", 0x2bcf) ||
        !attribute_matches(port, "idProduct", 0x1258)) {
        fprintf(stderr, "Camera identity mismatch\n");
        return 1;
    }
    char path[256], resolved[PATH_MAX], sysfs[PATH_MAX + 64], actual[PATH_MAX], suffix[64];
    snprintf(path, sizeof path,
        "/dev/v4l/by-path/pci-0000:06:00.4-usb-0:%s:1.0-video-index0", port);
    if (!realpath(path, resolved)) {
        perror("camera path");
        return 1;
    }
    const char *device = strrchr(resolved, '/');
    if (!device || strncmp(device + 1, "video", 5))
        return 1;
    snprintf(sysfs, sizeof sysfs, "/sys/class/video4linux/%s/device", device + 1);
    snprintf(suffix, sizeof suffix, "/3-%s:1.0", port);
    if (!realpath(sysfs, actual) || strlen(actual) < strlen(suffix) ||
        strcmp(actual + strlen(actual) - strlen(suffix), suffix)) {
        fprintf(stderr, "Video topology does not match selected camera\n");
        return 1;
    }
    struct stat info;
    if (stat(resolved, &info) || !S_ISCHR(info.st_mode) || major(info.st_rdev) != 81)
        return 1;
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        perror("read-only camera open");
        return 1;
    }
    printf("{\"bus\":3,\"port\":\"%s\",\"vid_pid\":\"2bcf:1258\","
           "\"stream_started\":false,\"controls\":[", port);
    struct v4l2_queryctrl control = {.id = V4L2_CTRL_FLAG_NEXT_CTRL};
    int first = 1;
    while (ioctl(fd, VIDIOC_QUERYCTRL, &control) == 0) {
        if (!(control.flags & V4L2_CTRL_FLAG_DISABLED)) {
            struct v4l2_control current = {.id = control.id};
            int got = ioctl(fd, VIDIOC_G_CTRL, &current);
            if (!first)
                putchar(',');
            first = 0;
            printf("{\"id\":%u,\"name\":", control.id);
            control.name[sizeof control.name - 1] = 0;
            string(control.name);
            printf(",\"type\":%u,\"min\":%d,\"max\":%d,\"step\":%d,"
                   "\"default\":%d,\"flags\":%u,\"value\":",
                   control.type, control.minimum, control.maximum, control.step,
                   control.default_value, control.flags);
            if (!got)
                printf("%d", current.value);
            else
                printf("null");
            printf(",\"menu\":[");
            int menu_first = 1;
            if (control.type == V4L2_CTRL_TYPE_MENU) {
                for (int index = control.minimum; index <= control.maximum; index++) {
                    struct v4l2_querymenu menu = {.id = control.id, .index = (unsigned)index};
                    if (ioctl(fd, VIDIOC_QUERYMENU, &menu))
                        continue;
                    if (!menu_first)
                        putchar(',');
                    menu_first = 0;
                    printf("{\"value\":%d,\"name\":", index);
                    menu.name[sizeof menu.name - 1] = 0;
                    string(menu.name);
                    putchar('}');
                }
            }
            printf("]}");
        }
        control.id |= V4L2_CTRL_FLAG_NEXT_CTRL;
    }
    printf("],\"mjpeg_1280x720_intervals\":[");
    first = 1;
    for (unsigned index = 0; index < 64; index++) {
        struct v4l2_frmivalenum interval = {
            .index = index, .pixel_format = V4L2_PIX_FMT_MJPEG,
            .width = 1280, .height = 720
        };
        if (ioctl(fd, VIDIOC_ENUM_FRAMEINTERVALS, &interval))
            break;
        if (!first)
            putchar(',');
        first = 0;
        if (interval.type == V4L2_FRMIVAL_TYPE_DISCRETE) {
            printf("{\"type\":\"discrete\",\"numerator\":%u,\"denominator\":%u}",
                   interval.discrete.numerator, interval.discrete.denominator);
        } else {
            printf("{\"type\":\"stepwise\",\"min\":[%u,%u],\"max\":[%u,%u],\"step\":[%u,%u]}",
                   interval.stepwise.min.numerator, interval.stepwise.min.denominator,
                   interval.stepwise.max.numerator, interval.stepwise.max.denominator,
                   interval.stepwise.step.numerator, interval.stepwise.step.denominator);
            break;
        }
    }
    printf("]}\n");
    close(fd);
    return 0;
}
