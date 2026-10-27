/* STM32L476RG 接口骨架：保留传感器和显示引脚规划，尚未完成实物驱动验证。 */
#include "bsp.h"
/* STM32L476RG reset clock is MSI 4MHz. No sensor driver is claimed available.
 * I2C1 PB6/PB7 MAX30102, I2C2 PB10/PB11 LIS2DW12, SPI1 PA5/PA7 OLED.
 * On real hardware implement these reads and clock/IRQ mapping after electrical validation. */
uint32_t SystemCoreClock=4000000;
/* 当前函数不操作外设，传感器可用性固定返回否，防止产生伪硬件数据。 */
void bsp_init(void){}
void bsp_putc(char c){(void)c;}
int bsp_getc(void){return -1;}
int bsp_sensor_available(void){return 0;}
int bsp_read_observation(float *ppg,float *accel){(void)ppg;(void)accel;return 0;}
