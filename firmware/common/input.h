/* 主机与固件共享的输入布局；字段变动必须同步升级协议版本。 */
#ifndef REPULSE_INPUT_H
#define REPULSE_INPUT_H

#include <stdint.h>
#include "dsp.h"

/* Fixed, versioned little-endian files in the per-session transport directory. */
#define INPUT_VERSION 2u
#define INPUT_SAMPLE_HZ 64u
#define INPUT_PERIOD_US 15625u

/* 文件头包含记录时间、策略、回放速度及头部和载荷的 CRC32。 */
typedef struct {
    char magic[8];
    uint32_t version, sample_hz, count, t0_us, duration_us, payload_crc32;
    uint32_t strategy, phase_us, width_us, speed_milli, header_crc32;
} InputHeader;

/* 单条观测保留原始时间、序号和缺口标志，采集开关由固件随后应用。 */
typedef struct {
    uint32_t time_us, seq;
    float ppg, accel;
    uint32_t gap, reserved;
} InputRow;

int input_open(void);
int input_read(InputRow *rows, unsigned count);
int input_valid(void);
const char *input_error(void);
const InputHeader *input_header(void);
uint32_t input_rows(void);
uint32_t input_crc32(void);
int output_write(const SampleBlock *block);
uint32_t output_rows(void);
uint32_t output_crc32(void);
void input_close(void);
#endif
