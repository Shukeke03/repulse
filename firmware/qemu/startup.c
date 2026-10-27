/* Cortex-M3 启动入口：初始化 C 运行所需内存，再进入固件 main。 */
#include <stdint.h>
extern uint32_t _estack,_sidata,_sdata,_edata,_sbss,_ebss;
extern int main(void);
/* 将已初始化数据复制到 RAM，并将未初始化数据区清零。 */
void Reset_Handler(void){uint32_t *src=&_sidata,*dst=&_sdata;while(dst<&_edata)*dst++=*src++;dst=&_sbss;while(dst<&_ebss)*dst++=0;main();for(;;){}}
void Default_Handler(void){for(;;){}}
void SVC_Handler(void);void PendSV_Handler(void);void SysTick_Handler(void);
/* 向量表保留 FreeRTOS 的 SVC、PendSV、SysTick 入口，其余异常停在默认处理器。 */
__attribute__((section(".isr_vector"),used))
const uintptr_t vectors[80]={
 (uintptr_t)&_estack,(uintptr_t)Reset_Handler,(uintptr_t)Default_Handler,(uintptr_t)Default_Handler,
 (uintptr_t)Default_Handler,(uintptr_t)Default_Handler,(uintptr_t)Default_Handler,0,0,0,0,
 (uintptr_t)SVC_Handler,(uintptr_t)Default_Handler,0,(uintptr_t)PendSV_Handler,(uintptr_t)SysTick_Handler
};
