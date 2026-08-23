#ifndef AIRGAP_TEST_ARDUINO_H
#define AIRGAP_TEST_ARDUINO_H

#include <stdint.h>
#include <stddef.h>

#include <deque>
#include <sstream>
#include <string>
#include <vector>

#define F(value) value

constexpr uint8_t LOW = 0;
constexpr uint8_t HIGH = 1;
constexpr uint8_t INPUT = 0;
constexpr uint8_t OUTPUT = 1;
constexpr uint8_t INPUT_PULLUP = 2;
constexpr uint8_t A0 = 14;

namespace fake {

struct PinWrite {
  uint8_t pin;
  uint8_t value;
};

extern uint32_t now_ms;
extern int digital_pins[32];
extern int analog_pins[32];
extern std::vector<PinWrite> pin_writes;
extern std::vector<int> servo_angles;
extern std::vector<unsigned int> tone_frequencies;

void reset();

}  // namespace fake

class HardwareSerial {
 public:
  void begin(unsigned long baud) { baud_ = baud; }
  int available() const { return static_cast<int>(input_.size()); }
  int read() {
    const unsigned char value = input_.front();
    input_.pop_front();
    return value;
  }
  size_t print(const char* value) {
    output_ += value;
    return std::string(value).size();
  }
  size_t print(char value) {
    output_ += value;
    return 1;
  }
  size_t print(unsigned char value) { return print(static_cast<unsigned int>(value)); }
  size_t print(signed char value) { return print(static_cast<int>(value)); }
  template <typename T>
  size_t print(T value) {
    std::ostringstream stream;
    stream << value;
    output_ += stream.str();
    return stream.str().size();
  }
  size_t println() {
    output_ += '\n';
    return 1;
  }
  template <typename T>
  size_t println(T value) {
    const size_t written = print(value);
    output_ += '\n';
    return written + 1;
  }
  void feed(const std::string& value) {
    input_.insert(input_.end(), value.begin(), value.end());
  }
  std::string takeOutput() {
    const std::string result = output_;
    output_.clear();
    return result;
  }
  unsigned long baud() const { return baud_; }

 private:
  unsigned long baud_ = 0;
  std::deque<unsigned char> input_;
  std::string output_;
};

extern HardwareSerial Serial;

inline unsigned long millis() { return fake::now_ms; }
inline void pinMode(uint8_t, uint8_t) {}
inline int digitalRead(uint8_t pin) { return fake::digital_pins[pin]; }
inline int analogRead(uint8_t pin) { return fake::analog_pins[pin]; }
inline void digitalWrite(uint8_t pin, uint8_t value) {
  fake::digital_pins[pin] = value;
  fake::pin_writes.push_back({pin, value});
}
inline void tone(uint8_t, unsigned int frequency) {
  fake::tone_frequencies.push_back(frequency);
}
inline void noTone(uint8_t) { fake::tone_frequencies.push_back(0); }

#endif
