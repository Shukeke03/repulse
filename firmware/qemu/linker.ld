ENTRY(Reset_Handler)
MEMORY { FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 4M
 RAM (rwx) : ORIGIN = 0x20000000, LENGTH = 4M }
_estack = ORIGIN(RAM)+LENGTH(RAM);
SECTIONS {
 .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
 .text : { *(.text*) *(.rodata*) KEEP(*(.init)) KEEP(*(.fini)) } > FLASH
 .ARM.extab : { *(.ARM.extab*) } > FLASH
 .ARM.exidx : { *(.ARM.exidx*) } > FLASH
 _sidata = LOADADDR(.data);
 .data : { . = ALIGN(4); _sdata = .; *(.data*) . = ALIGN(4); _edata = .; } > RAM AT> FLASH
 .bss : { . = ALIGN(8); _sbss = .; *(.bss*) *(COMMON) . = ALIGN(8); _ebss = .; end = .; } > RAM
 /DISCARD/ : { *(.comment) *(.note*) }
}
