/* Cortex-M4 启动入口：初始化内存、向量表基址和 FPU，然后进入固件 main。 */
#include <stdint.h>
extern uint32_t _estack,_sidata,_sdata,_edata,_sbss,_ebss;
extern int main(void);
/* .data 从 FLASH 复制到 RAM，.bss 清零；屏障确保 FPU 配置生效。 */
void Reset_Handler(void){uint32_t *src=&_sidata,*dst=&_sdata;while(dst<&_edata)*dst++=*src++;dst=&_sbss;while(dst<&_ebss)*dst++=0;*((volatile uint32_t*)0xE000ED08)=0x08000000;*((volatile uint32_t*)0xE000ED88)|=0xF<<20;__asm volatile("dsb; isb");main();for(;;){}}
void Default_Handler(void){for(;;){}}
void SVC_Handler(void);void PendSV_Handler(void);void SysTick_Handler(void);
/* FreeRTOS 接管 SVC、PendSV 和 SysTick；未实现的异常进入默认停机处理。 */
__attribute__((section(".isr_vector"),used))
const uintptr_t vectors[80]={
 (uintptr_t)&_estack,(uintptr_t)Reset_Handler,(uintptr_t)Default_Handler,(uintptr_t)Default_Handler,
 (uintptr_t)Default_Handler,(uintptr_t)Default_Handler,(uintptr_t)Default_Handler,0,0,0,0,
 (uintptr_t)SVC_Handler,(uintptr_t)Default_Handler,0,(uintptr_t)PendSV_Handler,(uintptr_t)SysTick_Handler
};
