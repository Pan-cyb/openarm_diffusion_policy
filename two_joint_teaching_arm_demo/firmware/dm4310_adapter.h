#ifndef DM4310_ADAPTER_H
#define DM4310_ADAPTER_H

#include <stdbool.h>
#include <stdint.h>

typedef struct {
  float position_rad;
  float velocity_rad_s;
  float torque_nm;
  float temperature_c;
  uint32_t fault_bits;
  uint32_t last_feedback_ms;
} dm4310_feedback_t;

/*
 * Implement these functions from the exact motor firmware manual or the
 * matching official OpenArm/Damiao CAN library. This project intentionally
 * does not guess the byte-level protocol.
 */
bool dm4310_enable(uint16_t send_id);
bool dm4310_disable(uint16_t send_id);
bool dm4310_clear_fault(uint16_t send_id);
bool dm4310_send_position(uint16_t send_id, float position_rad,
                          float max_speed_rad_s);
bool dm4310_read_feedback(uint16_t receive_id, dm4310_feedback_t *feedback);

#endif
