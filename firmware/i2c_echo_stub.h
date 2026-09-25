#ifndef I2C_ECHO_STUB_H
#define I2C_ECHO_STUB_H

#define ECHO_STUB_ADDRESS 0x50
#define ECHO_STUB_SDA 4
#define ECHO_STUB_SCL 5

// Call on core 0 before starting the bridge's core 1 polling loop.
void i2c_echo_stub_init(void);
#endif
