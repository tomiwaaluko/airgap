#include <Arduino.h>
#include <Servo.h>
#include <Wire.h>

#include <limits.h>
#include <stdlib.h>
#include <string.h>

constexpr uint8_t PIN_BTN_APPROVE = 2;
constexpr uint8_t PIN_BTN_DENY = 3;
constexpr uint8_t PIN_BTN_NEVER = 4;
constexpr uint8_t PIN_LED_GREEN = 5;
constexpr uint8_t PIN_LED_RED = 6;
constexpr uint8_t PIN_RELAY = 7;
constexpr uint8_t PIN_PIEZO = 8;
constexpr uint8_t PIN_SERVO_FLAG = 9;
constexpr uint8_t PIN_DIAL = A0;

constexpr uint8_t FLAG_DOWN_DEGREES = 0;
constexpr uint8_t FLAG_UP_DEGREES = 90;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 25;
constexpr uint32_t RELAY_LEASE_MS = 10000;
constexpr uint32_t TICK_INTERVAL_MS = 1000;
constexpr size_t MAX_FRAME_BYTES = 200;
constexpr uint8_t LCD_ADDRESS = 0x27;

constexpr char CMD_PING[] = "ping";
constexpr char CMD_LED[] = "led";
constexpr char CMD_TONE[] = "tone";
constexpr char CMD_FLAG[] = "flag";
constexpr char CMD_RELAY[] = "relay";
constexpr char CMD_RELAY_RENEW[] = "relay_renew";
constexpr char CMD_LCD[] = "lcd";
constexpr char CMD_ARM[] = "arm";
constexpr char CMD_DISARM[] = "disarm";

constexpr char ERR_UNKNOWN_CMD[] = "unknown_cmd";
constexpr char ERR_BAD_FIELD[] = "bad_field";
constexpr char ERR_OUT_OF_RANGE[] = "out_of_range";
constexpr char ERR_NOT_ARMED[] = "not_armed";
constexpr char ERR_NOT_CLOSED[] = "not_closed";
constexpr char ERR_BUSY[] = "busy";

constexpr char EVENT_BUTTON[] = "btn";
constexpr char EVENT_BOOT[] = "boot";
constexpr char EVENT_LEASE_EXPIRED[] = "lease_expired";
constexpr char EVENT_TICK[] = "tick";

constexpr char LED_OFF[] = "off";
constexpr char LED_GREEN[] = "green";
constexpr char LED_AMBER[] = "amber";
constexpr char LED_RED[] = "red";

constexpr char TONE_OK[] = "ok";
constexpr char TONE_DENY[] = "deny";
constexpr char TONE_ALERT[] = "alert";

constexpr char BUTTON_APPROVE[] = "approve";
constexpr char BUTTON_DENY[] = "deny";
constexpr char BUTTON_NEVER[] = "never";

enum JsonType : uint8_t {
  JSON_MISSING,
  JSON_STRING,
  JSON_NUMBER,
  JSON_BOOL,
  JSON_NULL_VALUE,
};

struct JsonValue {
  JsonType type;
  char text[18];
  long number;
  bool boolean;
  bool truncated;
};

struct ParsedCommand {
  JsonValue id;
  JsonValue cmd;
  JsonValue state;
  JsonValue pattern;
  JsonValue n;
  JsonValue up;
  JsonValue closed;
  JsonValue l1;
  JsonValue l2;
  JsonValue req;
};

struct ButtonState {
  uint8_t pin;
  uint8_t bit;
  const char* name;
  bool raw_pressed;
  bool stable_pressed;
  uint32_t raw_changed_at;
};

Servo flag_servo;

char serial_buffer[MAX_FRAME_BYTES];
size_t serial_length = 0;
bool serial_overflow = false;
uint32_t dropped_frames = 0;

bool armed = false;
char armed_request[9] = "";
uint8_t suppressed_buttons = 0;

bool relay_closed = false;
uint32_t lease_started_at = 0;

ButtonState buttons[] = {
    {PIN_BTN_APPROVE, 0x01, BUTTON_APPROVE, false, false, 0},
    {PIN_BTN_DENY, 0x02, BUTTON_DENY, false, false, 0},
    {PIN_BTN_NEVER, 0x04, BUTTON_NEVER, false, false, 0},
};

bool dial_initialized = false;
int32_t dial_filtered_eighths = 0;
uint16_t dial_stable_raw = 0;
uint8_t dial_level = 0;
uint32_t last_tick_at = 0;

bool tone_active = false;
bool tone_sounding = false;
uint8_t tone_beeps_remaining = 0;
uint16_t tone_frequency = 0;
uint16_t tone_duration_ms = 0;
uint32_t tone_next_at = 0;

bool lcd_pending = false;
bool lcd_started = false;
bool lcd_initialized = false;
uint8_t lcd_phase = 0;
uint8_t lcd_index = 0;
uint32_t lcd_next_at = 0;
char lcd_line_1[17] = "";
char lcd_line_2[17] = "";

bool reached(uint32_t now, uint32_t deadline) {
  return static_cast<int32_t>(now - deadline) >= 0;
}

void skipWhitespace(const char*& cursor) {
  while (*cursor == ' ' || *cursor == '\t' || *cursor == '\r' ||
         *cursor == '\n') {
    ++cursor;
  }
}

bool parseString(const char*& cursor, char* output, size_t output_size,
                 bool& truncated) {
  if (*cursor != '"') {
    return false;
  }
  ++cursor;
  size_t length = 0;
  truncated = false;
  while (*cursor != '\0' && *cursor != '"') {
    char value = *cursor++;
    if (static_cast<uint8_t>(value) < 0x20) {
      return false;
    }
    if (value == '\\') {
      value = *cursor++;
      if (value == '\0') {
        return false;
      }
      if (value == 'b') {
        value = '\b';
      } else if (value == 'f') {
        value = '\f';
      } else if (value == 'n') {
        value = '\n';
      } else if (value == 'r') {
        value = '\r';
      } else if (value == 't') {
        value = '\t';
      } else if (value != '"' && value != '\\' && value != '/') {
        return false;
      }
    }
    if (length + 1 < output_size) {
      output[length++] = value;
    } else {
      truncated = true;
    }
  }
  if (*cursor != '"') {
    return false;
  }
  ++cursor;
  output[length] = '\0';
  return true;
}

bool parseNumber(const char*& cursor, long& output) {
  bool negative = false;
  if (*cursor == '-') {
    negative = true;
    ++cursor;
  }
  if (*cursor < '0' || *cursor > '9') {
    return false;
  }
  long value = 0;
  bool overflow = false;
  while (*cursor >= '0' && *cursor <= '9') {
    const uint8_t digit = static_cast<uint8_t>(*cursor++ - '0');
    if (value > (LONG_MAX - digit) / 10) {
      overflow = true;
    } else if (!overflow) {
      value = value * 10 + digit;
    }
  }
  if (overflow) {
    output = negative ? LONG_MIN : LONG_MAX;
  } else {
    output = negative ? -value : value;
  }
  return true;
}

bool parseValue(const char*& cursor, JsonValue& value) {
  memset(&value, 0, sizeof(value));
  skipWhitespace(cursor);
  if (*cursor == '"') {
    value.type = JSON_STRING;
    return parseString(cursor, value.text, sizeof(value.text), value.truncated);
  }
  if (strncmp(cursor, "true", 4) == 0) {
    cursor += 4;
    value.type = JSON_BOOL;
    value.boolean = true;
    return true;
  }
  if (strncmp(cursor, "false", 5) == 0) {
    cursor += 5;
    value.type = JSON_BOOL;
    value.boolean = false;
    return true;
  }
  if (strncmp(cursor, "null", 4) == 0) {
    cursor += 4;
    value.type = JSON_NULL_VALUE;
    return true;
  }
  value.type = JSON_NUMBER;
  return parseNumber(cursor, value.number);
}

JsonValue* fieldFor(ParsedCommand& command, const char* key) {
  if (strcmp(key, "id") == 0) return &command.id;
  if (strcmp(key, "cmd") == 0) return &command.cmd;
  if (strcmp(key, "state") == 0) return &command.state;
  if (strcmp(key, "pattern") == 0) return &command.pattern;
  if (strcmp(key, "n") == 0) return &command.n;
  if (strcmp(key, "up") == 0) return &command.up;
  if (strcmp(key, "closed") == 0) return &command.closed;
  if (strcmp(key, "l1") == 0) return &command.l1;
  if (strcmp(key, "l2") == 0) return &command.l2;
  if (strcmp(key, "req") == 0) return &command.req;
  return nullptr;
}

bool parseCommand(const char* frame, ParsedCommand& command) {
  memset(&command, 0, sizeof(command));
  const char* cursor = frame;
  skipWhitespace(cursor);
  if (*cursor++ != '{') {
    return false;
  }
  skipWhitespace(cursor);
  if (*cursor == '}') {
    ++cursor;
    skipWhitespace(cursor);
    return *cursor == '\0';
  }
  while (*cursor != '\0') {
    char key[12];
    bool key_truncated = false;
    if (!parseString(cursor, key, sizeof(key), key_truncated)) {
      return false;
    }
    skipWhitespace(cursor);
    if (*cursor++ != ':') {
      return false;
    }
    JsonValue parsed;
    if (!parseValue(cursor, parsed)) {
      return false;
    }
    if (!key_truncated) {
      JsonValue* destination = fieldFor(command, key);
      if (destination != nullptr) {
        *destination = parsed;
      }
    }
    skipWhitespace(cursor);
    if (*cursor == '}') {
      ++cursor;
      skipWhitespace(cursor);
      return *cursor == '\0';
    }
    if (*cursor++ != ',') {
      return false;
    }
    skipWhitespace(cursor);
  }
  return false;
}

void sendAck(long id) {
  Serial.print(F("{\"id\":"));
  Serial.print(id);
  Serial.println(F(",\"ok\":true}"));
}

void sendError(long id, const char* error) {
  Serial.print(F("{\"id\":"));
  Serial.print(id);
  Serial.print(F(",\"ok\":false,\"err\":\""));
  Serial.print(error);
  Serial.println(F("\"}"));
}

void setLed(const char* state) {
  const bool green = strcmp(state, LED_GREEN) == 0 ||
                     strcmp(state, LED_AMBER) == 0;
  const bool red = strcmp(state, LED_RED) == 0 || strcmp(state, LED_AMBER) == 0;
  digitalWrite(PIN_LED_GREEN, green ? HIGH : LOW);
  digitalWrite(PIN_LED_RED, red ? HIGH : LOW);
}

void openRelay() {
  digitalWrite(PIN_RELAY, LOW);
  relay_closed = false;
  lease_started_at = 0;
}

uint8_t heldButtonBits() {
  uint8_t bits = 0;
  for (ButtonState& button : buttons) {
    if (button.stable_pressed) {
      bits |= button.bit;
    }
  }
  return bits;
}

uint8_t physicalButtonBits() {
  uint8_t bits = 0;
  for (ButtonState& button : buttons) {
    if (digitalRead(button.pin) == LOW) {
      bits |= button.bit;
    }
  }
  return bits;
}

bool validRequestId(const JsonValue& req) {
  if (req.type != JSON_STRING || req.truncated || strlen(req.text) != 8) {
    return false;
  }
  for (uint8_t index = 0; index < 8; ++index) {
    const char value = req.text[index];
    if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f'))) {
      return false;
    }
  }
  return true;
}

void startTonePattern(const char* pattern, long requested_count, uint32_t now) {
  const uint8_t count = requested_count < 1
                            ? 1
                            : (requested_count > 5 ? 5 : requested_count);
  noTone(PIN_PIEZO);
  if (strcmp(pattern, TONE_OK) == 0) {
    tone_frequency = 1500;
    tone_duration_ms = 80;
  } else if (strcmp(pattern, TONE_DENY) == 0) {
    tone_frequency = 250;
    tone_duration_ms = 300;
  } else {
    tone_frequency = 1000;
    tone_duration_ms = 120;
  }
  tone_beeps_remaining = count;
  tone_active = true;
  tone_sounding = true;
  tone(PIN_PIEZO, tone_frequency);
  tone_next_at = now + tone_duration_ms;
}

void queueLcd(const char* line_1, const char* line_2, uint32_t now) {
  strncpy(lcd_line_1, line_1, 16);
  strncpy(lcd_line_2, line_2, 16);
  lcd_line_1[16] = '\0';
  lcd_line_2[16] = '\0';
  if (!lcd_started) {
    Wire.begin();
    lcd_started = true;
    lcd_phase = 0;
    lcd_next_at = now + 50;
  } else if (lcd_initialized) {
    lcd_phase = 8;
    lcd_index = 0;
    lcd_next_at = now;
  }
  lcd_pending = true;
}

bool validTextField(const JsonValue& value) {
  return value.type == JSON_STRING && !value.truncated;
}

void handleCommand(const ParsedCommand& command, uint32_t now) {
  if (command.id.type != JSON_NUMBER) {
    ++dropped_frames;
    return;
  }
  const long id = command.id.number;
  if (id < 1 || id > 65535) {
    sendError(id, ERR_OUT_OF_RANGE);
    return;
  }
  if (command.cmd.type != JSON_STRING || command.cmd.truncated) {
    sendError(id, ERR_BAD_FIELD);
    return;
  }
  const char* name = command.cmd.text;
  if (strcmp(name, CMD_PING) == 0) {
    sendAck(id);
  } else if (strcmp(name, CMD_LED) == 0) {
    if (command.state.type != JSON_STRING || command.state.truncated) {
      sendError(id, ERR_BAD_FIELD);
    } else if (strcmp(command.state.text, LED_OFF) != 0 &&
               strcmp(command.state.text, LED_GREEN) != 0 &&
               strcmp(command.state.text, LED_AMBER) != 0 &&
               strcmp(command.state.text, LED_RED) != 0) {
      sendError(id, ERR_OUT_OF_RANGE);
    } else {
      setLed(command.state.text);
      sendAck(id);
    }
  } else if (strcmp(name, CMD_TONE) == 0) {
    if (command.pattern.type != JSON_STRING || command.pattern.truncated ||
        command.n.type != JSON_NUMBER) {
      sendError(id, ERR_BAD_FIELD);
    } else if (strcmp(command.pattern.text, TONE_OK) != 0 &&
               strcmp(command.pattern.text, TONE_DENY) != 0 &&
               strcmp(command.pattern.text, TONE_ALERT) != 0) {
      sendError(id, ERR_OUT_OF_RANGE);
    } else {
      startTonePattern(command.pattern.text, command.n.number, now);
      sendAck(id);
    }
  } else if (strcmp(name, CMD_FLAG) == 0) {
    if (command.up.type != JSON_BOOL) {
      sendError(id, ERR_BAD_FIELD);
    } else {
      flag_servo.write(command.up.boolean ? FLAG_UP_DEGREES : FLAG_DOWN_DEGREES);
      sendAck(id);
    }
  } else if (strcmp(name, CMD_RELAY) == 0) {
    if (command.closed.type != JSON_BOOL) {
      sendError(id, ERR_BAD_FIELD);
    } else if (!command.closed.boolean) {
      openRelay();
      sendAck(id);
    } else if (!armed) {
      sendError(id, ERR_NOT_ARMED);
    } else if (relay_closed) {
      sendError(id, ERR_BUSY);
    } else {
      digitalWrite(PIN_RELAY, HIGH);
      relay_closed = true;
      lease_started_at = now;
      sendAck(id);
    }
  } else if (strcmp(name, CMD_RELAY_RENEW) == 0) {
    if (!relay_closed) {
      sendError(id, ERR_NOT_CLOSED);
    } else {
      lease_started_at = now;
      sendAck(id);
    }
  } else if (strcmp(name, CMD_LCD) == 0) {
    if (!validTextField(command.l1) || !validTextField(command.l2)) {
      sendError(id, ERR_BAD_FIELD);
    } else {
      queueLcd(command.l1.text, command.l2.text, now);
      sendAck(id);
    }
  } else if (strcmp(name, CMD_ARM) == 0) {
    if (!validRequestId(command.req)) {
      sendError(id, ERR_BAD_FIELD);
    } else if (armed) {
      sendError(id, ERR_BUSY);
    } else {
      strcpy(armed_request, command.req.text);
      suppressed_buttons = physicalButtonBits();
      armed = true;
      sendAck(id);
    }
  } else if (strcmp(name, CMD_DISARM) == 0) {
    armed = false;
    armed_request[0] = '\0';
    suppressed_buttons = 0;
    sendAck(id);
  } else {
    sendError(id, ERR_UNKNOWN_CMD);
  }
}

void serviceSerial(uint32_t now) {
  bool line_complete = false;
  while (Serial.available() > 0 && !line_complete) {
    const char value = static_cast<char>(Serial.read());
    if (value == '\r') {
      continue;
    }
    if (value == '\n') {
      if (serial_overflow) {
        ++dropped_frames;
      } else {
        serial_buffer[serial_length] = '\0';
        ParsedCommand command;
        if (parseCommand(serial_buffer, command)) {
          handleCommand(command, now);
        } else {
          ++dropped_frames;
        }
      }
      serial_length = 0;
      serial_overflow = false;
      line_complete = true;
    } else if (!serial_overflow) {
      if (serial_length < sizeof(serial_buffer) - 1) {
        serial_buffer[serial_length++] = value;
      } else {
        serial_overflow = true;
      }
    }
  }
}

void emitButton(const ButtonState& button, uint32_t now) {
  Serial.print(F("{\"ev\":\""));
  Serial.print(EVENT_BUTTON);
  Serial.print(F("\",\"which\":\""));
  Serial.print(button.name);
  Serial.print(F("\",\"req\":"));
  if (armed) {
    Serial.print('"');
    Serial.print(armed_request);
    Serial.print('"');
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"t\":"));
  Serial.print(now);
  Serial.println('}');
}

void serviceButtons(uint32_t now) {
  for (ButtonState& button : buttons) {
    const bool pressed = digitalRead(button.pin) == LOW;
    if (pressed != button.raw_pressed) {
      button.raw_pressed = pressed;
      button.raw_changed_at = now;
    }
    if (!button.raw_pressed && (suppressed_buttons & button.bit) != 0 &&
        now - button.raw_changed_at >= BUTTON_DEBOUNCE_MS) {
      suppressed_buttons &= static_cast<uint8_t>(~button.bit);
    }
    if (button.stable_pressed != button.raw_pressed &&
        now - button.raw_changed_at >= BUTTON_DEBOUNCE_MS) {
      button.stable_pressed = button.raw_pressed;
      if (!button.stable_pressed) {
        suppressed_buttons &= static_cast<uint8_t>(~button.bit);
      } else if ((suppressed_buttons & button.bit) == 0) {
        emitButton(button, now);
      }
    }
  }
}

void serviceDial() {
  const uint16_t raw = static_cast<uint16_t>(analogRead(PIN_DIAL));
  if (!dial_initialized) {
    dial_filtered_eighths = static_cast<int32_t>(raw) * 8;
    dial_stable_raw = raw;
    dial_initialized = true;
  } else {
    const int32_t target = static_cast<int32_t>(raw) * 8;
    dial_filtered_eighths += (target - dial_filtered_eighths) / 8;
    const uint16_t filtered =
        static_cast<uint16_t>((dial_filtered_eighths + 4) / 8);
    if (filtered > dial_stable_raw + 1 || filtered + 1 < dial_stable_raw) {
      dial_stable_raw = filtered;
    }
  }
  dial_level = static_cast<uint8_t>(
      (static_cast<uint32_t>(dial_stable_raw) * 10U) / 1023U);
}

void serviceTone(uint32_t now) {
  if (!tone_active || !reached(now, tone_next_at)) {
    return;
  }
  if (tone_sounding) {
    noTone(PIN_PIEZO);
    --tone_beeps_remaining;
    if (tone_beeps_remaining == 0) {
      tone_active = false;
      return;
    }
    tone_sounding = false;
    tone_next_at = now + 100;
  } else {
    tone(PIN_PIEZO, tone_frequency);
    tone_sounding = true;
    tone_next_at = now + tone_duration_ms;
  }
}

void lcdWriteNibble(uint8_t nibble, bool data) {
  const uint8_t base = static_cast<uint8_t>((nibble << 4) | 0x08 |
                                             (data ? 0x01 : 0x00));
  Wire.beginTransmission(LCD_ADDRESS);
  Wire.write(base);
  Wire.write(static_cast<uint8_t>(base | 0x04));
  Wire.write(base);
  Wire.endTransmission();
}

void lcdWriteByte(uint8_t value, bool data) {
  lcdWriteNibble(static_cast<uint8_t>(value >> 4), data);
  lcdWriteNibble(static_cast<uint8_t>(value & 0x0F), data);
}

char lcdCharacter(const char* line, uint8_t index) {
  const size_t length = strlen(line);
  return index < length ? line[index] : ' ';
}

void serviceLcd(uint32_t now) {
  if (!lcd_pending || !reached(now, lcd_next_at)) {
    return;
  }
  if (!lcd_initialized) {
    if (lcd_phase <= 2) {
      lcdWriteNibble(0x03, false);
      lcd_next_at = now + (lcd_phase == 0 ? 5 : 1);
    } else if (lcd_phase == 3) {
      lcdWriteNibble(0x02, false);
      lcd_next_at = now + 1;
    } else {
      const uint8_t commands[] = {0x28, 0x0C, 0x06, 0x01};
      lcdWriteByte(commands[lcd_phase - 4], false);
      lcd_next_at = now + (lcd_phase == 7 ? 2 : 1);
    }
    ++lcd_phase;
    if (lcd_phase == 8) {
      lcd_initialized = true;
      lcd_index = 0;
    }
    return;
  }
  if (lcd_phase == 8) {
    lcdWriteByte(0x80, false);
    lcd_phase = 9;
    lcd_index = 0;
  } else if (lcd_phase == 9) {
    lcdWriteByte(static_cast<uint8_t>(lcdCharacter(lcd_line_1, lcd_index)), true);
    if (++lcd_index == 16) {
      lcd_phase = 10;
    }
  } else if (lcd_phase == 10) {
    lcdWriteByte(0xC0, false);
    lcd_phase = 11;
    lcd_index = 0;
  } else {
    lcdWriteByte(static_cast<uint8_t>(lcdCharacter(lcd_line_2, lcd_index)), true);
    if (++lcd_index == 16) {
      lcd_pending = false;
    }
  }
  lcd_next_at = now + 1;
}

uint32_t leaseRemaining(uint32_t now) {
  if (!relay_closed) {
    return 0;
  }
  const uint32_t age = now - lease_started_at;
  return age >= RELAY_LEASE_MS ? 0 : RELAY_LEASE_MS - age;
}

void serviceLease(uint32_t now) {
  if (!relay_closed || now - lease_started_at < RELAY_LEASE_MS) {
    return;
  }
  openRelay();
  Serial.print(F("{\"ev\":\""));
  Serial.print(EVENT_LEASE_EXPIRED);
  Serial.print(F("\",\"t\":"));
  Serial.print(now);
  Serial.println('}');
}

void serviceTick(uint32_t now) {
  if (now - last_tick_at < TICK_INTERVAL_MS) {
    return;
  }
  last_tick_at = now;
  Serial.print(F("{\"ev\":\""));
  Serial.print(EVENT_TICK);
  Serial.print(F("\",\"dial\":"));
  Serial.print(dial_level);
  Serial.print(F(",\"relay\":"));
  Serial.print(relay_closed ? F("true") : F("false"));
  Serial.print(F(",\"armed\":"));
  Serial.print(armed ? F("true") : F("false"));
  Serial.print(F(",\"lease_ms\":"));
  Serial.print(leaseRemaining(now));
  Serial.print(F(",\"btns\":"));
  Serial.print(heldButtonBits());
  Serial.print(F(",\"t\":"));
  Serial.print(now);
  Serial.println('}');
}

void setup() {
  digitalWrite(PIN_RELAY, LOW);
  pinMode(PIN_RELAY, OUTPUT);
  relay_closed = false;

  flag_servo.attach(PIN_SERVO_FLAG);
  flag_servo.write(FLAG_UP_DEGREES);

  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  setLed(LED_RED);

  for (ButtonState& button : buttons) {
    pinMode(button.pin, INPUT_PULLUP);
    button.raw_pressed = digitalRead(button.pin) == LOW;
    button.stable_pressed = button.raw_pressed;
    button.raw_changed_at = static_cast<uint32_t>(millis());
  }
  pinMode(PIN_PIEZO, OUTPUT);
  pinMode(PIN_DIAL, INPUT);

  Serial.begin(115200);
  const uint32_t now = static_cast<uint32_t>(millis());
  Serial.print(F("{\"ev\":\""));
  Serial.print(EVENT_BOOT);
  Serial.print(F("\",\"fw\":\"1.0.0\",\"t\":"));
  Serial.print(now);
  Serial.println('}');
  armed = false;
  armed_request[0] = '\0';
  last_tick_at = now;
}

void loop() {
  const uint32_t now = static_cast<uint32_t>(millis());
  serviceLease(now);
  serviceSerial(now);
  serviceButtons(now);
  serviceDial();
  serviceTone(now);
  serviceLcd(now);
  serviceTick(now);
}
