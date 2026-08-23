#include "Arduino.h"
#include "Servo.h"
#include "Wire.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace fake {

uint32_t now_ms = 0;
int digital_pins[32];
int analog_pins[32];
std::vector<PinWrite> pin_writes;
std::vector<int> servo_angles;
std::vector<unsigned int> tone_frequencies;

void reset() {
  now_ms = 0;
  pin_writes.clear();
  servo_angles.clear();
  tone_frequencies.clear();
  for (int& pin : digital_pins) {
    pin = HIGH;
  }
  for (int& pin : analog_pins) {
    pin = 512;
  }
}

}  // namespace fake

HardwareSerial Serial;
TwoWire Wire;

#include "../../firmware/airgap/airgap.ino"

namespace {

void require(bool condition, const std::string& message) {
  if (!condition) {
    std::cerr << message << '\n';
    std::exit(1);
  }
}

bool contains(const std::string& text, const std::string& part) {
  return text.find(part) != std::string::npos;
}

std::string command(const std::string& frame) {
  Serial.feed(frame + "\n");
  loop();
  return Serial.takeOutput();
}

void at(uint32_t time_ms) {
  fake::now_ms = time_ms;
  loop();
}

void start() {
  fake::reset();
  setup();
}

void bootScenario() {
  start();
  require(!fake::pin_writes.empty(), "boot made no pin writes");
  require(fake::pin_writes.front().pin == 7 &&
              fake::pin_writes.front().value == LOW,
          "relay LOW was not the first boot write");
  require(!fake::servo_angles.empty() && fake::servo_angles.front() == 90,
          "flag did not boot UP");
  require(fake::digital_pins[7] == LOW, "relay did not boot open");
  require(fake::digital_pins[6] == HIGH, "red LED did not boot on");
  require(Serial.baud() == 115200, "serial baud is not 115200");
  require(contains(Serial.takeOutput(),
                   "{\"ev\":\"boot\",\"fw\":\"1.0.0\",\"t\":0}"),
          "boot event is missing or malformed");
}

void commandsScenario() {
  start();
  Serial.takeOutput();
  require(command("{\"id\":1,\"cmd\":\"ping\"}") ==
              "{\"id\":1,\"ok\":true}\n",
          "ping ack mismatch");
  require(command("{\"cmd\":\"led\",\"state\":\"amber\",\"id\":2}") ==
              "{\"id\":2,\"ok\":true}\n",
          "led ack mismatch");
  require(fake::digital_pins[5] == HIGH && fake::digital_pins[6] == HIGH,
          "amber did not drive both LED channels");
  require(command(
              "{\"id\":3,\"cmd\":\"tone\",\"pattern\":\"alert\",\"n\":9}") ==
              "{\"id\":3,\"ok\":true}\n",
          "tone ack mismatch");
  require(!fake::tone_frequencies.empty(), "tone did not start");
  require(command("{\"id\":4,\"cmd\":\"flag\",\"up\":false}") ==
              "{\"id\":4,\"ok\":true}\n",
          "flag ack mismatch");
  require(fake::servo_angles.back() == 0, "flag did not move down");
  require(command("{\"id\":5,\"cmd\":\"relay\",\"closed\":true}") ==
              "{\"id\":5,\"ok\":false,\"err\":\"not_armed\"}\n",
          "disarmed relay close was not rejected");
  require(command(
              "{\"id\":6,\"cmd\":\"lcd\",\"l1\":\"DROP users\",\"l2\":\"irreversible\"}") ==
              "{\"id\":6,\"ok\":true}\n",
          "lcd ack mismatch");
  require(command("{\"id\":7,\"cmd\":\"arm\",\"req\":\"a91f3c2e\"}") ==
              "{\"id\":7,\"ok\":true}\n",
          "arm ack mismatch");
  require(command("{\"id\":8,\"cmd\":\"relay\",\"closed\":true}") ==
              "{\"id\":8,\"ok\":true}\n",
          "armed relay close failed");
  require(fake::digital_pins[7] == HIGH, "relay close did not drive HIGH");
  require(command("{\"id\":9,\"cmd\":\"relay_renew\"}") ==
              "{\"id\":9,\"ok\":true}\n",
          "closed relay renewal failed");
  require(command("{\"id\":10,\"cmd\":\"relay\",\"closed\":false}") ==
              "{\"id\":10,\"ok\":true}\n",
          "relay open failed");
  require(command("{\"id\":11,\"cmd\":\"disarm\"}") ==
              "{\"id\":11,\"ok\":true}\n",
          "disarm ack mismatch");
  require(command("{\"id\":12,\"cmd\":\"surprise\"}") ==
              "{\"id\":12,\"ok\":false,\"err\":\"unknown_cmd\"}\n",
          "unknown command ack mismatch");
}

void buttonsDisarmedScenario() {
  start();
  Serial.takeOutput();
  const uint8_t pins[] = {2, 3, 4};
  const char* names[] = {"approve", "deny", "never"};
  uint32_t time_ms = 1;
  for (uint8_t index = 0; index < 3; ++index) {
    fake::digital_pins[pins[index]] = LOW;
    at(time_ms);
    at(time_ms + 25);
    const std::string output = Serial.takeOutput();
    require(contains(output, std::string("\"which\":\"") + names[index] + "\""),
            "button identity mismatch");
    require(contains(output, "\"req\":null"),
            "disarmed button did not carry req:null");
    fake::digital_pins[pins[index]] = HIGH;
    at(time_ms + 26);
    at(time_ms + 51);
    Serial.takeOutput();
    time_ms += 60;
  }
}

void heldButtonScenario() {
  fake::reset();
  fake::digital_pins[2] = LOW;
  setup();
  Serial.takeOutput();
  require(command("{\"id\":1,\"cmd\":\"arm\",\"req\":\"a91f3c2e\"}") ==
              "{\"id\":1,\"ok\":true}\n",
          "arm failed while button held");
  at(100);
  require(!contains(Serial.takeOutput(), "\"ev\":\"btn\""),
          "held button emitted after arm");
  fake::digital_pins[2] = HIGH;
  at(101);
  at(126);
  Serial.takeOutput();
  fake::digital_pins[2] = LOW;
  at(127);
  at(152);
  const std::string output = Serial.takeOutput();
  require(contains(output, "\"which\":\"approve\""),
          "released and re-pressed button did not emit");
  require(contains(output, "\"req\":\"a91f3c2e\""),
          "armed button did not echo request id");
}

void heldButtonShortPressScenario() {
  start();
  Serial.takeOutput();
  fake::digital_pins[2] = LOW;
  loop();
  require(command("{\"id\":1,\"cmd\":\"arm\",\"req\":\"a91f3c2e\"}") ==
              "{\"id\":1,\"ok\":true}\n",
          "arm failed during short held press");
  fake::digital_pins[2] = HIGH;
  at(10);
  at(35);
  Serial.takeOutput();
  fake::digital_pins[2] = LOW;
  at(36);
  at(61);
  const std::string output = Serial.takeOutput();
  require(contains(output, "\"which\":\"approve\""),
          "debounced release did not clear held-button suppression");
  require(contains(output, "\"req\":\"a91f3c2e\""),
          "first post-release press did not echo request id");
}

void leaseExpiryScenario() {
  start();
  Serial.takeOutput();
  command("{\"id\":1,\"cmd\":\"arm\",\"req\":\"a91f3c2e\"}");
  command("{\"id\":2,\"cmd\":\"relay\",\"closed\":true}");
  at(9999);
  require(fake::digital_pins[7] == HIGH, "relay opened before lease deadline");
  require(!contains(Serial.takeOutput(), "lease_expired"),
          "lease expired early");
  at(10000);
  const std::string output = Serial.takeOutput();
  require(fake::digital_pins[7] == LOW, "relay stayed closed at lease expiry");
  require(contains(output, "{\"ev\":\"lease_expired\",\"t\":10000}"),
          "lease expiry event missing");
}

void renewOpenScenario() {
  start();
  Serial.takeOutput();
  require(command("{\"id\":1,\"cmd\":\"relay_renew\"}") ==
              "{\"id\":1,\"ok\":false,\"err\":\"not_closed\"}\n",
          "open relay renewal did not ack not_closed");
  require(fake::digital_pins[7] == LOW, "open relay renewal closed the relay");
}

void lateRenewScenario() {
  start();
  Serial.takeOutput();
  command("{\"id\":1,\"cmd\":\"arm\",\"req\":\"a91f3c2e\"}");
  command("{\"id\":2,\"cmd\":\"relay\",\"closed\":true}");
  fake::now_ms = 10000;
  Serial.feed("{\"id\":3,\"cmd\":\"relay_renew\"}\n");
  loop();
  const std::string output = Serial.takeOutput();
  require(fake::digital_pins[7] == LOW,
          "renewal at the deadline revived an expired lease");
  require(contains(output, "{\"ev\":\"lease_expired\",\"t\":10000}"),
          "deadline renewal suppressed lease_expired");
  require(contains(output,
                   "{\"id\":3,\"ok\":false,\"err\":\"not_closed\"}"),
          "deadline renewal did not observe the opened contact");
}

void tickScenario() {
  fake::reset();
  fake::analog_pins[A0] = 1023;
  fake::digital_pins[2] = LOW;
  fake::digital_pins[4] = LOW;
  setup();
  Serial.takeOutput();
  loop();
  at(1000);
  const std::string output = Serial.takeOutput();
  require(contains(output, "{\"ev\":\"tick\",\"dial\":10,\"relay\":false,"),
          "tick dial or relay field mismatch: " + output);
  require(contains(output, "\"armed\":false,\"lease_ms\":0,\"btns\":5,\"t\":1000}"),
          "tick state fields mismatch");
}

void malformedRecoveryScenario() {
  start();
  Serial.takeOutput();
  Serial.feed("{bad}\n{\"id\":2,\"cmd\":\"ping\"}\r\n");
  loop();
  require(Serial.takeOutput().empty(), "malformed frame was not dropped silently");
  loop();
  require(Serial.takeOutput() == "{\"id\":2,\"ok\":true}\n",
          "parser did not recover after malformed frame");
  Serial.feed(std::string(205, 'x') + "\n{\"id\":3,\"cmd\":\"ping\"}\n");
  loop();
  require(Serial.takeOutput().empty(), "overflow frame was not discarded");
  loop();
  require(Serial.takeOutput() == "{\"id\":3,\"ok\":true}\n",
          "line assembler did not recover after overflow");
}

}  // namespace

int main(int argc, char** argv) {
  require(argc == 2, "expected one scenario name");
  const std::string scenario = argv[1];
  if (scenario == "boot") {
    bootScenario();
  } else if (scenario == "commands") {
    commandsScenario();
  } else if (scenario == "buttons_disarmed") {
    buttonsDisarmedScenario();
  } else if (scenario == "held_button") {
    heldButtonScenario();
  } else if (scenario == "held_button_short_press") {
    heldButtonShortPressScenario();
  } else if (scenario == "lease_expiry") {
    leaseExpiryScenario();
  } else if (scenario == "late_renew") {
    lateRenewScenario();
  } else if (scenario == "renew_open") {
    renewOpenScenario();
  } else if (scenario == "tick") {
    tickScenario();
  } else if (scenario == "malformed_recovery") {
    malformedRecoveryScenario();
  } else {
    require(false, "unknown scenario");
  }
  return 0;
}
