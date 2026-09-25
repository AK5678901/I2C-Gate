#include "echo_fifo.h"

void echo_fifo_init(echo_fifo_t *fifo) {
    fifo->head = fifo->count = 0;
    fifo->underflows = fifo->overflows = 0;
}

bool echo_fifo_write(echo_fifo_t *fifo, uint8_t value) {
    if (fifo->count == ECHO_FIFO_CAPACITY) {
        ++fifo->overflows;
        return false; // Preserve unread data; discard the newest byte.
    }
    fifo->data[(fifo->head + fifo->count) % ECHO_FIFO_CAPACITY] = value;
    ++fifo->count;
    return true;
}

uint8_t echo_fifo_read(echo_fifo_t *fifo) {
    if (!fifo->count) {
        ++fifo->underflows;
        return 0xff;
    }
    uint8_t value = fifo->data[fifo->head];
    fifo->head = (fifo->head + 1) % ECHO_FIFO_CAPACITY;
    --fifo->count;
    return value;
}
