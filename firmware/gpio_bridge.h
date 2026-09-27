#ifndef GPIO_BRIDGE_H
#define GPIO_BRIDGE_H
#include "gate_config.h"
// GPIO numbers, not physical connector pin numbers.
#define UP_SDA 0
#define UP_SCL 1
#define DOWN_SDA 2
#define DOWN_SCL 3
extern config_t gate_configs[2];
extern volatile int gate_active_config;
extern volatile int gate_pending_config;
extern volatile uint32_t gate_faults;
void bridge_init(void);
// Register before launching core 1. NULL selects the built-in filter callback.
void bridge_set_callbacks(address_callback_t address, data_callback_t data);
void bridge_core1(void);
void bridge_service(void);
#endif
