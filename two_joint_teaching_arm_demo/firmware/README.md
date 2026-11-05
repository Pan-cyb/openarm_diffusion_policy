# MCU 固件骨架

本目录不是某块单片机的可直接烧录工程，而是平台无关的安全和协议核心。

移植到 STM32、ESP32、RP2040 或其他 MCU 时需要实现：

```text
board_millis
board_estop_active
board_pc_receive
board_status
board_delay_ms

dm4310_enable
dm4310_disable
dm4310_clear_fault
dm4310_send_position
dm4310_read_feedback
```

其中 `dm4310_*` 必须来自与实物电机固件匹配的官方 CAN 协议。不要把
`dm4310_adapter.h` 当成已经完成的电机驱动。

语法检查：

```bash
gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only \
  firmware/protocol.c firmware/main.c
```
