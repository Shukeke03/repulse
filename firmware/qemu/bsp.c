/* MPS2-AN385 板级适配：25 MHz 时钟及 CMSDK UART，观测由文件输入提供。 */
#include "bsp.h"
#define UART ((volatile uint32_t*)0x40004000UL)
uint32_t SystemCoreClock=25000000;
/* 设置串口分频并启用收发；读取为空时返回 -1，供会话任务轮询。 */
void bsp_init(void){UART[4]=217;UART[2]=3;}
void bsp_putc(char c){while(UART[1]&1){} UART[0]=(uint32_t)c;}
int bsp_getc(void){if(!(UART[1]&2))return -1;return (int)(UART[0]&255);}
/* QEMU 没有连接物理传感器，不能将文件回放宣称为硬件采集。 */
int bsp_sensor_available(void){return 0;}
int bsp_read_observation(float *ppg,float *accel){(void)ppg;(void)accel;return 0;}
