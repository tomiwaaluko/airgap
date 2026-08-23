#ifndef AIRGAP_TEST_WIRE_H
#define AIRGAP_TEST_WIRE_H

#include "Arduino.h"

class TwoWire {
 public:
  void begin() {}
  void beginTransmission(uint8_t) {}
  size_t write(uint8_t) { return 1; }
  uint8_t endTransmission() { return 0; }
};

extern TwoWire Wire;

#endif
