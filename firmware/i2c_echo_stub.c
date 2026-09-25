#include "i2c_echo_stub.h"
#include "echo_fifo.h"
#include "pico/stdlib.h"
#include "pico/i2c_slave.h"

// Inspect count/underflows/overflows with a debugger. Only the ISR modifies it.
static echo_fifo_t echo_fifo;

static void receive_pending(i2c_inst_t *i2c) {
    while (i2c_get_read_available(i2c))
        (void)echo_fifo_write(&echo_fifo, i2c_read_byte_raw(i2c));
}

static void echo_handler(i2c_inst_t *i2c, i2c_slave_event_t event) {
    switch (event) {
    case I2C_SLAVE_RECEIVE:
    case I2C_SLAVE_FINISH:
        // FINISH can precede RECEIVE when STOP/RESTART and RX are pending
        // together. Drain RX here as well, retaining data across boundaries.
        receive_pending(i2c);
        break;
    case I2C_SLAVE_REQUEST:
        receive_pending(i2c);
        // Supply exactly one byte per request. Prefilling TX would consume
        // unread queue entries beyond the master's final NACK.
        i2c_write_byte_raw(i2c, echo_fifo_read(&echo_fifo));
        break;
    }
}

void i2c_echo_stub_init(void) {
    echo_fifo_init(&echo_fifo);
    i2c_init(i2c0, 100000);
    const unsigned pins[] = { ECHO_STUB_SDA, ECHO_STUB_SCL };
    for (unsigned i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
        gpio_set_function(pins[i], GPIO_FUNC_I2C);
        gpio_disable_pulls(pins[i]); // External pull-ups on the downstream bus.
    }
    // Hardware captures the bus while core 0 bit-bangs DOWN_SDA/DOWN_SCL.
    // IRQs on core 0 remain enabled, including while waiting for stretching.
    i2c_slave_init(i2c0, ECHO_STUB_ADDRESS, echo_handler);
}
