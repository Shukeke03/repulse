#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H
#include <stdint.h>
extern uint32_t SystemCoreClock;
void repulse_assert(const char *,int);
#define configUSE_PREEMPTION 1
#define configUSE_TIME_SLICING 1
#define configCPU_CLOCK_HZ (SystemCoreClock)
#define configTICK_RATE_HZ ((TickType_t)1000)
#define configMAX_PRIORITIES 8
#define configMINIMAL_STACK_SIZE 256
#define configTOTAL_HEAP_SIZE 0
#define configMAX_TASK_NAME_LEN 16
#define configUSE_16_BIT_TICKS 0
#define configIDLE_SHOULD_YIELD 1
#define configUSE_MUTEXES 0
#define configUSE_COUNTING_SEMAPHORES 0
#define configQUEUE_REGISTRY_SIZE 8
#define configCHECK_FOR_STACK_OVERFLOW 2
#define configUSE_MALLOC_FAILED_HOOK 0
#define configSUPPORT_STATIC_ALLOCATION 1
#define configSUPPORT_DYNAMIC_ALLOCATION 0
#define configUSE_TIMERS 0
#define configUSE_TRACE_FACILITY 1
#define configGENERATE_RUN_TIME_STATS 0
#define configUSE_IDLE_HOOK 1
#define configUSE_TICK_HOOK 0
#define INCLUDE_vTaskDelay 1
#define INCLUDE_vTaskDelayUntil 1
#define INCLUDE_uxTaskGetStackHighWaterMark 1
#define INCLUDE_eTaskGetState 1
#define INCLUDE_xTaskGetIdleTaskHandle 1
#define configPRIO_BITS 3
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY 7
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY 5
#define configKERNEL_INTERRUPT_PRIORITY (7 << 5)
#define configMAX_SYSCALL_INTERRUPT_PRIORITY (5 << 5)
#define configASSERT(x) do { if(!(x))repulse_assert(__FILE__,__LINE__); }while(0)
#define vPortSVCHandler SVC_Handler
#define xPortPendSVHandler PendSV_Handler
#define xPortSysTickHandler SysTick_Handler
#endif
