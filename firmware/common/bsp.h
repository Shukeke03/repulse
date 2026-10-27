/* 板级接口：串口控制通道及预留的真实传感器读取入口。 */
#ifndef REPULSE_BSP_H
#define REPULSE_BSP_H
#include <stdint.h>
void bsp_init(void);
void bsp_putc(char c);
int bsp_getc(void);
int bsp_sensor_available(void);
int bsp_read_observation(float *ppg,float *accel);
#endif
