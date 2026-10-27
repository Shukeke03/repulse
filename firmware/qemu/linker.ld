/* MPS2-AN385 仿真内存布局；初始栈顶放在 RAM 末端。 */
ENTRY(Reset_Handler)
MEMORY { FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 4M
 RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 4M }
_estack = ORIGIN(RAM)+LENGTH(RAM);
SECTIONS {
 /* 向量表和代码驻留 FLASH，启动时将 .data 装载内容复制到 RAM。 */
 .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
 .text : { *(.text*) *(.rodata*) KEEP(*(.init)) KEEP(*(.fini)) } > FLASH
 .ARM.extab : { *(.ARM.extab*) } > FLASH
 .ARM.exidx : { *(.ARM.exidx*) } > FLASH
 _sidata = LOADADDR(.data);
 .data : { . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT> FLASH
 /* .bss 的边界由复位入口使用，保证静态队列和任务栈初始为零。 */
 .bss : { . = ALIGN(8); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(8); _ebss = .; end = .; } > RAM
 /DISCARD/ : { *(.comment) *(.note*) }
}
