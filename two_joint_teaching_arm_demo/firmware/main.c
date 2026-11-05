#include "dm4310_adapter.h"
#include "protocol.h"

#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum controller_state {
  STATE_DISABLED,
  STATE_ARMED,
  STATE_RUNNING,
  STATE_FAULT,
};

static const uint16_t MOTOR_SEND_ID[2] = {0x01u, 0x02u};
static const uint16_t MOTOR_RECEIVE_ID[2] = {0x11u, 0x12u};
static const float JOINT_MIN[2] = {-1.20f, -1.40f};
static const float JOINT_MAX[2] = {1.20f, 1.40f};
static const uint32_t PC_WATCHDOG_MS = 100u;
static const uint32_t CAN_WATCHDOG_MS = 50u;

/* Board/HAL functions supplied by the selected MCU platform. */
extern uint32_t board_millis(void);
extern bool board_estop_active(void);
extern bool board_pc_receive(uint8_t *buffer, size_t capacity, size_t *length);
extern void board_status(enum controller_state state);
extern void board_delay_ms(uint32_t delay_ms);

static float clampf(float value, float lower, float upper) {
  if (value < lower) return lower;
  if (value > upper) return upper;
  return value;
}

static void disable_all(void) {
  (void)dm4310_disable(MOTOR_SEND_ID[0]);
  (void)dm4310_disable(MOTOR_SEND_ID[1]);
}

int main(void) {
  enum controller_state state = STATE_DISABLED;
  uint32_t last_pc_ms = 0u;
  uint32_t last_sequence = 0u;
  bool have_sequence = false;
  float target[2] = {0.25f, -0.50f};
  uint8_t packet_bytes[sizeof(ta_command_packet_t)];

  disable_all();
  for (;;) {
    const uint32_t now = board_millis();
    dm4310_feedback_t feedback[2] = {0};
    const bool feedback_ok =
        dm4310_read_feedback(MOTOR_RECEIVE_ID[0], &feedback[0]) &&
        dm4310_read_feedback(MOTOR_RECEIVE_ID[1], &feedback[1]);

    if (board_estop_active() ||
        (state != STATE_DISABLED &&
         (!feedback_ok ||
          now - feedback[0].last_feedback_ms > CAN_WATCHDOG_MS ||
          now - feedback[1].last_feedback_ms > CAN_WATCHDOG_MS)) ||
        ((state == STATE_ARMED || state == STATE_RUNNING) &&
         now - last_pc_ms > PC_WATCHDOG_MS)) {
      disable_all();
      state = STATE_FAULT;
    }

    size_t packet_length = 0u;
    if (board_pc_receive(packet_bytes, sizeof(packet_bytes), &packet_length)) {
      ta_command_packet_t command;
      if (ta_decode_command(packet_bytes, packet_length, &command) &&
          (!have_sequence || command.sequence > last_sequence)) {
        have_sequence = true;
        last_sequence = command.sequence;
        last_pc_ms = now;

        if (command.kind == TA_CMD_CLEAR_FAULT && !board_estop_active()) {
          state = STATE_DISABLED;
        } else if (command.kind == TA_CMD_DISARM) {
          disable_all();
          state = STATE_DISABLED;
        } else if (command.kind == TA_CMD_ARM && state == STATE_DISABLED &&
                   feedback_ok) {
          if (dm4310_enable(MOTOR_SEND_ID[0]) &&
              dm4310_enable(MOTOR_SEND_ID[1])) {
            state = STATE_ARMED;
          } else {
            state = STATE_FAULT;
          }
        } else if (command.kind == TA_CMD_TARGET &&
                   (state == STATE_ARMED || state == STATE_RUNNING)) {
          target[0] = clampf(command.q1_rad, JOINT_MIN[0], JOINT_MAX[0]);
          target[1] = clampf(command.q2_rad, JOINT_MIN[1], JOINT_MAX[1]);
          const bool sent =
              dm4310_send_position(MOTOR_SEND_ID[0], target[0],
                                   fmaxf(0.0f, command.max_speed1_rad_s)) &&
              dm4310_send_position(MOTOR_SEND_ID[1], target[1],
                                   fmaxf(0.0f, command.max_speed2_rad_s));
          state = sent ? STATE_RUNNING : STATE_FAULT;
        }
      }
    }

    if (state == STATE_FAULT) {
      disable_all();
    }
    board_status(state);
    board_delay_ms(1u);
  }
}
