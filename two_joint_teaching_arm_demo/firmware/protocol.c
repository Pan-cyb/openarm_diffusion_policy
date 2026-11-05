#include "protocol.h"

#include <string.h>

uint32_t ta_crc32(const uint8_t *data, size_t length) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    for (unsigned bit = 0; bit < 8; ++bit) {
      const uint32_t mask = (uint32_t)-(int32_t)(crc & 1u);
      crc = (crc >> 1u) ^ (0xEDB88320u & mask);
    }
  }
  return ~crc;
}

bool ta_decode_command(const uint8_t *bytes, size_t length,
                       ta_command_packet_t *output) {
  if (bytes == NULL || output == NULL || length != sizeof(*output)) {
    return false;
  }
  memcpy(output, bytes, sizeof(*output));
  if (output->magic[0] != TA_MAGIC0 || output->magic[1] != TA_MAGIC1 ||
      output->version != TA_VERSION) {
    return false;
  }
  const uint32_t expected =
      ta_crc32(bytes, sizeof(*output) - sizeof(output->crc32));
  return expected == output->crc32;
}
