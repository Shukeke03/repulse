TARGET ?= qemu
TOOLCHAIN ?= arm-none-eabi-
CC := $(TOOLCHAIN)gcc
SIZE := $(TOOLCHAIN)size
KERNEL := vendor/FreeRTOS-Kernel
BUILD := build/$(TARGET)
COMMON := common/main.c common/dsp.c $(KERNEL)/tasks.c $(KERNEL)/queue.c $(KERNEL)/list.c
ifeq ($(TARGET),qemu)
CPU := -mcpu=cortex-m3 -mthumb
PORT := $(KERNEL)/portable/GCC/ARM_CM3
DEFINE := -DREPULSE_QEMU=1
else ifeq ($(TARGET),stm32)
CPU := -mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=softfp
PORT := $(KERNEL)/portable/GCC/ARM_CM4F
DEFINE := -DREPULSE_STM32=1
else
$(error unsupported TARGET=$(TARGET))
endif
SOURCES := $(COMMON) $(TARGET)/startup.c $(TARGET)/bsp.c $(PORT)/port.c
CFLAGS := $(CPU) $(DEFINE) -std=c11 -Os -g3 -Wall -Wextra -Wno-misleading-indentation -ffunction-sections -fdata-sections -fno-common -Icommon -I$(KERNEL)/include -I$(PORT)
LDFLAGS := $(CPU) -T$(TARGET)/linker.ld -nostartfiles --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections,-Map=$(BUILD)/repulse.map
.PHONY: all run debug clean
all: $(BUILD)/repulse.elf
$(BUILD)/repulse.elf: $(SOURCES) common/FreeRTOSConfig.h $(TARGET)/linker.ld common/dsp.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $(SOURCES) $(LDFLAGS) -lm -lc -lgcc -o $@
	$(SIZE) $@
run: all
	qemu-system-arm -M mps2-an385 -cpu cortex-m3 -kernel $(BUILD)/repulse.elf -display none -monitor none -serial stdio
# GDB: arm-none-eabi-gdb build/qemu/repulse.elf -ex 'target remote :3333'
debug: all
	qemu-system-arm -M mps2-an385 -cpu cortex-m3 -kernel $(BUILD)/repulse.elf -display none -monitor none -serial stdio -S -gdb tcp::3333
clean:
	rm -rf build
