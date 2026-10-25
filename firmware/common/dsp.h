#ifndef REPULSE_DSP_H
#define REPULSE_DSP_H
#include <stdint.h>
#define DSP_CAPACITY 1000
#define REASON_NONE 0
#define REASON_INSUFFICIENT 1
#define REASON_GAP 2
#define REASON_WARMUP 3
#define REASON_POOR_SIGNAL 4
#define REASON_INTERVALS 5
#define REASON_AUTOCORR 6
#define REASON_NO_CONTACT 7
#define REASON_RANGE 8
#define REASON_PENDING 9
typedef struct { uint32_t time_us, seq; uint16_t segment; uint8_t on,gap; float ppg,accel; } Sample;
typedef struct { uint8_t count; Sample values[16]; float filtered[16]; } SampleBlock;
typedef struct { uint32_t available_us,start_us,end_us; int valid,reason; float pulse_bpm; } Result;
void dsp_reset(void);
float dsp_last_filtered(void);
void dsp_feed(const Sample *sample);
Result dsp_estimate(uint32_t start_us,uint32_t end_us,uint32_t available_us);
const char *dsp_reason(int reason);
#endif
