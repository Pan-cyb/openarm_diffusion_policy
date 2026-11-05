#ifndef TEACHING_ARM_PROTOCOL_H
#define TEACHING_ARM_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define TA_MAGIC0 ((uint8_t)'T')
#define TA_MAGIC1 ((uint8_t)'A')
#define TA_VERSION 1u

typedef enum {
  TA_CMD_ARM = 1,
  TA_CMD_TARGET = 2,
  TA_CMD_DISARM = 3,
  TA_CMD_CLEAR_FAULT = 4,
} ta_command_kind_t;

#pragma pack(push, 1)
typedef struct {
  uint8_t magic[2];
  uint8_t version;
  uint8_t kind;
  uint32_t sequence;
  float q1_rad;
  float q2_rad;
  float max_speed1_rad_s;
  float max_speed2_rad_s;
  uint32_t crc32;
} ta_command_packet_t;
#pragma pack(pop)

uint32_t ta_crc32(const uint8_t *data, size_t length);
bool ta_decode_command(const uint8_t *bytes, size_t length,
                       ta_command_packet_t *output);

#endif
