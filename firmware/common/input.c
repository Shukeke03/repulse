/* 真实记录的二进制输入与固件处理结果输出；序号、时间和 CRC 共同校验完整性。 */
#include "input.h"
#include <stddef.h>
#include <string.h>

_Static_assert(sizeof(InputHeader) == 52, "input header layout");
_Static_assert(sizeof(InputRow) == 24, "input sample layout");

typedef struct {
    uint32_t time_us, seq;
    uint16_t segment;
    uint8_t on, gap;
    float ppg, accel, filtered;
} OutputRow;
_Static_assert(sizeof(OutputRow) == 24, "output sample layout");

static InputHeader header;
static uint32_t read_count, written_count, input_crc = 0xffffffffu, output_crc = 0xffffffffu;
static int reader = -1, writer = -1, verified;
static const char *failure = "SOURCE_UNAVAILABLE";

#ifdef REPULSE_QEMU
static uint32_t crc32_update(uint32_t crc, const void *data, unsigned length) {
    const uint8_t *bytes = data;
    while (length--) {
        crc ^= *bytes++;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return crc;
}

/* ARM semihosting ABI; only these trusted fixed relative filenames are used.
 * UART remains a control channel, not a bulk sample stream. */
static int host_call(unsigned operation, void *parameters) {
    register unsigned r0 __asm("r0") = operation;
    register void *r1 __asm("r1") = parameters;
    __asm volatile("bkpt 0xab" : "+r"(r0) : "r"(r1) : "memory");
    return (int)r0;
}
static int host_open(const char *path, unsigned mode) {
    uintptr_t args[3] = {(uintptr_t)path, mode, strlen(path)};
    return host_call(0x01, args);
}
/* semihosting 返回未读取字节数，因此循环处理短读，无法前进即报错。 */
static int host_read(int handle, void *data, unsigned length) {
    uint8_t *position = data;
    while (length) {
        uintptr_t args[3] = {(uintptr_t)handle, (uintptr_t)position, length};
        int left = host_call(0x06, args);
        if (left < 0 || (unsigned)left >= length) return 0;
        position += length - (unsigned)left;
        length = (unsigned)left;
    }
    return 1;
}
static int host_write(int handle, const void *data, unsigned length) {
    uintptr_t args[3] = {(uintptr_t)handle, (uintptr_t)data, length};
    return host_call(0x05, args) == 0;
}
static void host_close(int handle) {
    if (handle >= 0) {
        uintptr_t args[1] = {(uintptr_t)handle};
        (void)host_call(0x02, args);
    }
}
#endif

/* 校验固定版本的文件头后才开放数据源，STM32 未接传感器时明确不可用。 */
int input_open(void) {
#ifdef REPULSE_QEMU
    reader = host_open("observations.bin", 1);
    if (reader < 0 || !host_read(reader, &header, sizeof(header))) {
        failure = "INPUT_FILE_MISSING";
        input_close();
        return 0;
    }
    if (memcmp(header.magic, "RPLREAL2", 8) || header.version != INPUT_VERSION ||
        header.sample_hz != INPUT_SAMPLE_HZ || header.count == 0 || header.count > 200000 ||
        header.t0_us < 6000000 || header.t0_us % INPUT_PERIOD_US ||
        header.duration_us != header.count * INPUT_PERIOD_US ||
        header.duration_us != header.t0_us + 124000000 || header.strategy > 2 ||
        header.phase_us >= 20000000 || header.width_us > 20000000 ||
        header.speed_milli < 500 || header.speed_milli > 10000 ||
        (crc32_update(0xffffffffu, &header, offsetof(InputHeader, header_crc32)) ^ 0xffffffffu) != header.header_crc32) {
        failure = "INVALID_INPUT_HEADER";
        input_close();
        return 0;
    }
    writer = host_open("processed.bin", 5);
    const uint32_t output_header[4] = {0x4f4c5052u, 0x32305455u, INPUT_VERSION, INPUT_SAMPLE_HZ};
    if (writer < 0 || !host_write(writer, output_header, sizeof(output_header))) {
        failure = "OUTPUT_FILE_ERROR";
        input_close();
        return 0;
    }
    failure = "NONE";
    return 1;
#else
    failure = "SENSOR_UNAVAILABLE";
    return 0;
#endif
}

/* 按块读取并验证严格递增的样本序号和 15625 微秒间隔，末块核对完整 CRC。 */
int input_read(InputRow *rows, unsigned count) {
#ifdef REPULSE_QEMU
    if (reader < 0 || strcmp(failure, "NONE")) return -1;
    if (read_count == header.count) return 0;
    if (count > header.count - read_count) count = header.count - read_count;
    if (!host_read(reader, rows, count * sizeof(InputRow))) {
        failure = "TRUNCATED_INPUT";
        return -1;
    }
    input_crc = crc32_update(input_crc, rows, count * sizeof(InputRow));
    for (unsigned i = 0; i < count; ++i) {
        if (rows[i].seq != read_count + i || rows[i].time_us != (read_count + i) * INPUT_PERIOD_US) {
            failure = "INPUT_SEQUENCE_ERROR";
            return -1;
        }
    }
    read_count += count;
    if (read_count == header.count) {
        if ((input_crc ^ 0xffffffffu) != header.payload_crc32) {
            failure = "INPUT_CRC_MISMATCH";
            return -1;
        }
        verified = 1;
    }
    return (int)count;
#else
    (void)rows; (void)count;
    return -1;
#endif
}

/* 输出采集掩码、原始值及 C 滤波结果，供主机归档和独立复核。 */
int output_write(const SampleBlock *block) {
#ifdef REPULSE_QEMU
    OutputRow rows[16];
    for (unsigned i = 0; i < block->count; ++i) {
        const Sample *s = &block->values[i];
        rows[i] = (OutputRow){s->time_us, s->seq, s->segment, s->on, s->gap,
                             s->ppg, s->accel, block->filtered[i]};
    }
    unsigned length = block->count * sizeof(OutputRow);
    if (writer < 0 || !host_write(writer, rows, length)) {
        failure = "OUTPUT_WRITE_FAILED";
        return 0;
    }
    output_crc = crc32_update(output_crc, rows, length);
    written_count += block->count;
    return 1;
#else
    (void)block;
    return 0;
#endif
}

int input_valid(void) { return verified && !strcmp(failure, "NONE"); }
const char *input_error(void) { return failure; }
const InputHeader *input_header(void) { return &header; }
uint32_t input_rows(void) { return read_count; }
uint32_t input_crc32(void) { return input_crc ^ 0xffffffffu; }
uint32_t output_rows(void) { return written_count; }
uint32_t output_crc32(void) { return output_crc ^ 0xffffffffu; }
/* 正常完成、取消和初始化失败均关闭已打开的主机文件。 */
void input_close(void) {
#ifdef REPULSE_QEMU
    host_close(reader);
    host_close(writer);
#endif
    reader = writer = -1;
}
