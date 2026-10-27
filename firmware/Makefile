# 固件构建入口；TARGET 选择 QEMU 仿真目标或 STM32 接口骨架。
TARGET ?= qemu
TOOLCHAIN ?= arm-none-eabi-
PYTHON ?= python
RUN_ARGS ?=
CC := $(TOOLCHAIN)gcc
SIZE := $(TOOLCHAIN)size
KERNEL := vendor/FreeRTOS-Kernel
BUILD := build/$(TARGET)
# 项目业务代码共用，内核代码和 Cortex-M 端口来自未修改的 FreeRTOS 源码。
COMMON := common/main.c common/dsp.c common/input.c $(KERNEL)/tasks.c $(KERNEL)/queue.c $(KERNEL)/list.c
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
# 按目标组合启动文件、BSP、内核端口及对应的链接内存布局。
SOURCES := $(COMMON) $(TARGET)/startup.c $(TARGET)/bsp.c $(PORT)/port.c
CFLAGS := $(CPU) $(DEFINE) -std=c11 -Os -g3 -Wall -Wextra -Wno-misleading-indentation -ffunction-sections -fdata-sections -fno-common -Icommon -I$(KERNEL)/include -I$(PORT)
LDFLAGS := $(CPU) -T$(TARGET)/linker.ld -nostartfiles --specs=nano.specs --specs=nosys.specs -Wl,--gc-sections,-Map=$(BUILD)/repulse.map
.PHONY: all run debug clean
all: $(BUILD)/repulse.elf
$(BUILD)/repulse.elf: $(SOURCES) common/FreeRTOSConfig.h $(TARGET)/linker.ld common/dsp.h common/input.h
	@mkdir -p $(BUILD)
	$(CC) $(CFLAGS) $(SOURCES) $(LDFLAGS) -lm -lc -lgcc -o $@
	$(SIZE) $@
# 启动脚本先准备本地真实记录输入，再运行固件；参数通过 RUN_ARGS 传入。
run: all
	cd .. && $(PYTHON) -m scripts.run_qemu $(RUN_ARGS)
# GDB: arm-none-eabi-gdb build/qemu/repulse.elf -ex 'target remote :3333'
# 调试启动时暂停 CPU，等待本机 GDB 连接后继续。
debug: all
	cd .. && $(PYTHON) -m scripts.run_qemu --gdb $(RUN_ARGS)
clean:
	rm -rf build
