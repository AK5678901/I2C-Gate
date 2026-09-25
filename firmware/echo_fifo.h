#ifndef ECHO_FIFO_H
#define ECHO_FIFO_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define ECHO_FIFO_CAPACITY 4096u

// Single owner: the stub's core 0 I2C interrupt handler.
typedef struct {
    uint8_t data[ECHO_FIFO_CAPACITY];
    size_t head, count;
    uint32_t underflows, overflows;
} echo_fifo_t;

void echo_fifo_init(echo_fifo_t *fifo);
bool echo_fifo_write(echo_fifo_t *fifo, uint8_t value);
uint8_t echo_fifo_read(echo_fifo_t *fifo);
#endif
