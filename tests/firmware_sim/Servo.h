#ifndef AIRGAP_TEST_SERVO_H
#define AIRGAP_TEST_SERVO_H

#include "Arduino.h"

class Servo {
 public:
  uint8_t attach(int pin) {
    pin_ = pin;
    attached_ = true;
    return 1;
  }
  void write(int angle) { fake::servo_angles.push_back(angle); }
  bool attached() const { return attached_; }
  void detach() { attached_ = false; }

 private:
  int pin_ = -1;
  bool attached_ = false;
};

#endif
