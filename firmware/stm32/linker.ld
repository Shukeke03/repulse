/* STM32L476RG 的 FLASH 与主 SRAM 布局，栈顶位于这段 RAM 末端。 */
ENTRY(Reset_Handler)
MEMORY { FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 1024K
 RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 96K }
_estack = ORIGIN(RAM)+LENGTH(RAM);
SECTIONS {
 /* 向量表和代码位于 FLASH；.data 的装载地址供复位入口复制使用。 */
 .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
 .text : { *(.text*) *(.rodata*) KEEP(*(.init)) KEEP(*(.fini)) } > FLASH
 .ARM.extab : { *(.ARM.extab*) } > FLASH
 .ARM.exidx : { *(.ARM.exidx*) } > FLASH
 _sidata = LOADADDR(.data);
 .data : { . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT> FLASH
 /* 启动时清零 .bss，确保静态任务、队列和 DSP 状态有确定初值。 */
 .bss : { . = ALIGN(8); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(8); _ebss = .; end = .; } > RAM
 /DISCARD/ : { *(.comment) *(.note*) }
}
