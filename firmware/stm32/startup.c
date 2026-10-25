#include <stdint.h>
extern uint32_t _estack,_sidata,_sdata,_edata,_sbss,_ebss;
extern int main(void);
void Reset_Handler(void){uint32_t *src=&_sidata,*dst=&_sdata;while(dst<&_edata)*dst++=*src++;dst=&_sbss;while(dst<&_ebss)*dst++=0;*((volatile uint32_t*)0xE000ED08)=0x08000000;*((volatile uint32_t*)0xE000ED88)|=0xF<<20;__asm volatile("dsb; isb");main();for(;;){}}
void Default_Handler(void){for(;;){}}
void SVC_Handler(void);void PendSV_Handler(void);void SysTick_Handler(void);
__attribute__((section(".isr_vector"),used))
const uintptr_t vectors[80]={
 (uintptr_t)&_estack,(uintptr_t)Reset_Handler,(uintptr_t)Default_Handler,(uintptr_t)Default_Handler,
 (uintptr_t)Default_Handler,(uintptr_t)Default_Handler,(uintptr_t)Default_Handler,0,0,0,0,
 (uintptr_t)SVC_Handler,(uintptr_t)Default_Handler,0,(uintptr_t)PendSV_Handler,(uintptr_t)SysTick_Handler
};
