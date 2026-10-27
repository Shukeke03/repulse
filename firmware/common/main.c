#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "bsp.h"
#include "dsp.h"
#include "input.h"
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define EVENT_LENGTH 256
static StaticQueue_t raw_control, ui_control, session_control, feedback_control, event_control;
static uint8_t raw_storage[8*sizeof(SampleBlock)], ui_storage[sizeof(Result)];
static uint8_t session_storage[sizeof(Result)], feedback_storage[sizeof(Result)];
static uint8_t event_storage[16*EVENT_LENGTH];
static QueueHandle_t qRaw, qUI, qSession, qFeedback, qEvents;
static StaticTask_t task_tcb[6];
static StackType_t acquisition_stack[512], dsp_stack[1536], session_stack[768];
static StackType_t feedback_stack[512], ui_stack[768], system_stack[1024];
static TaskHandle_t handles[6];
static volatile uint32_t heartbeat[6], revision, raw_drops, processed_us, prepare_tick;
static volatile uint32_t t0_us=UINT32_MAX;
static volatile int session_state, completion_target=4;
static int source_ready;
static Result latest, windows[3];
static const char *states[]={"CREATED","PREPARING","RECOVERING","FINALIZING","COMPLETED","CANCELLED","FAILED"};
static const char *task_names[]={"Acquisition","DSP","Session","Feedback","UI","System"};

static void line_out(const char *text) {
    while(*text)bsp_putc(*text++);
    bsp_putc('\n');
}
static void emit(const char *format,...) {
    char message[EVENT_LENGTH];
    va_list args;
    va_start(args,format);
    vsnprintf(message,sizeof(message),format,args);
    va_end(args);
    if(xQueueSend(qEvents,message,0)!=pdPASS) {
        raw_drops++;
        session_state=6;
    }
}
static void source_failure(const char *code) {
    session_state=6;
    revision++;
    emit("{\"type\":\"fatal\",\"code\":\"%s\"}",code);
}
static int acquisition_enabled(uint32_t time_us) {
    const InputHeader *h=input_header();
    if(time_us<h->t0_us)return 1;
    uint32_t relative=time_us-h->t0_us;
    if(relative>=124000000)return 0;
    if(h->strategy==0)return 1;
    if(h->strategy==2)return (relative+20000000-h->phase_us)%20000000<h->width_us;
    const uint32_t ranges[7][2]={{0,10000000},{20000000,30000000},{40000000,50000000},
        {54000000,64000000},{80000000,90000000},{100000000,110000000},{114000000,124000000}};
    for(unsigned i=0;i<7;i++)if(relative>=ranges[i][0]&&relative<ranges[i][1])return 1;
    return 0;
}

static void acquisition_task(void *unused) {
    (void)unused;
    uint16_t segment=0;
    int was_available=0;
    InputRow rows[16];
    for(;;) {
        heartbeat[0]++;
        if(session_state!=1&&session_state!=2) { vTaskDelay(pdMS_TO_TICKS(1));continue; }
        const InputHeader *h=input_header();
        if(input_rows()>=h->count) { vTaskDelay(pdMS_TO_TICKS(1));continue; }
        uint32_t next_count=h->count-input_rows();
        if(next_count>16)next_count=16;
        uint32_t next_end=(input_rows()+next_count)*INPUT_PERIOD_US;
        uint64_t playback_elapsed=(uint64_t)(xTaskGetTickCount()-prepare_tick)*h->speed_milli;
        if(playback_elapsed<next_end) { vTaskDelay(pdMS_TO_TICKS(1));continue; }
        int n=input_read(rows,next_count);
        if(n<=0) { source_failure(input_error());continue; }
        SampleBlock block={.count=(uint8_t)n};
        for(unsigned i=0;i<(unsigned)n;i++) {
            int on=acquisition_enabled(rows[i].time_us);
            int gap=rows[i].gap||!isfinite(rows[i].ppg)||!isfinite(rows[i].accel);
            int available=on&&!gap;
            if(available&&!was_available)segment++;
            was_available=available;
            block.values[i]=(Sample){.time_us=rows[i].time_us,.seq=rows[i].seq,
                .segment=segment,.on=(uint8_t)on,.gap=(uint8_t)(gap&&on),
                .ppg=available?rows[i].ppg:0.f,.accel=isfinite(rows[i].accel)?rows[i].accel:0.f};
        }
        if(xQueueSend(qRaw,&block,pdMS_TO_TICKS(100))!=pdPASS) {
            raw_drops++;
            source_failure("RAW_QUEUE_OVERFLOW");
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

static void dsp_task(void *unused) {
    (void)unused;
    SampleBlock block;
    uint32_t last_estimate=0;
    for(;;) {
        if(xQueueReceive(qRaw,&block,portMAX_DELAY)!=pdPASS)continue;
        heartbeat[1]++;
        if(session_state!=1&&session_state!=2)continue;
        const InputHeader *h=input_header();
        for(unsigned i=0;i<block.count;i++) {
            dsp_feed(&block.values[i]);
            block.filtered[i]=dsp_last_filtered();
        }
        uint32_t end=block.values[block.count-1].time_us+INPUT_PERIOD_US;
        processed_us=end;
        if(session_state==1&&end>=h->t0_us) {
            t0_us=h->t0_us;
            session_state=2;
            revision++;
            emit("{\"type\":\"event\",\"code\":\"RECOVERY_STARTED\",\"time_us\":%lu}",(unsigned long)t0_us);
        }
        if(end-last_estimate>=1000000) {
            last_estimate=end;
            Result result;
            if(end<8000000) {
                memset(&result,0,sizeof(result));
                result.reason=REASON_INSUFFICIENT;
                result.end_us=end;
            } else result=dsp_estimate(end-8000000,end,end);
            taskENTER_CRITICAL();latest=result;taskEXIT_CRITICAL();
            xQueueOverwrite(qUI,&result);
            xQueueOverwrite(qSession,&result);
            xQueueOverwrite(qFeedback,&result);
        }
        const Sample *last=&block.values[block.count-1];
        if(!last->on||last->gap||dsp_last_warming()) {
            taskENTER_CRITICAL();
            latest.valid=0;
            latest.reason=!last->on?REASON_OFF:last->gap?REASON_GAP:REASON_WARMUP;
            latest.end_us=end;
            taskEXIT_CRITICAL();
        }
        static const uint32_t centers[3]={0,60000000,120000000};
        for(unsigned i=0;i<3;i++)if(windows[i].reason==REASON_PENDING&&end>=h->t0_us+centers[i]+4000000) {
            Result result=dsp_estimate(h->t0_us+centers[i]-4000000,h->t0_us+centers[i]+4000000,end);
            taskENTER_CRITICAL();windows[i]=result;taskEXIT_CRITICAL();
        }
        if(!output_write(&block)) { source_failure(input_error());continue; }
        if(end>=h->duration_us) {
            if(!input_valid()) { source_failure(input_error());continue; }
            completion_target=4;
            session_state=3;
            revision++;
        }
    }
}

static int field_string(const char *json,const char *key,char *out,unsigned capacity) {
    char token[80];
    snprintf(token,sizeof(token),"\"%s\"",key);
    const char *position=strstr(json,token);
    if(!position)return 0;
    position+=strlen(token);
    while(*position==' '||*position=='\t')position++;
    if(*position++!=':')return 0;
    while(*position==' '||*position=='\t')position++;
    if(*position++!='\"')return 0;
    unsigned n=0;
    while(*position&&*position!='\"') {
        if((unsigned char)*position<32||*position=='\\'||n+1>=capacity)return 0;
        out[n++]=*position++;
    }
    if(*position!='\"')return 0;
    out[n]=0;
    return 1;
}
static int field_uint(const char *json,const char *key,uint32_t *out) {
    char token[80];
    snprintf(token,sizeof(token),"\"%s\"",key);
    const char *position=strstr(json,token);
    if(!position)return 0;
    position+=strlen(token);
    while(*position==' ')position++;
    if(*position++!=':')return 0;
    while(*position==' ')position++;
    if(*position<'0'||*position>'9')return 0;
    *out=(uint32_t)strtoul(position,0,10);
    return 1;
}
static void command(const char *json) {
    char kind[32],id[64];
    uint32_t expected=revision;
    if(!field_string(json,"type",kind,sizeof(kind))||!field_string(json,"command_id",id,sizeof(id))) {
        emit("{\"type\":\"ack\",\"applied\":false,\"code\":\"INVALID_PARAMETER\"}");
        return;
    }
    static char last_ids[8][64],last_kinds[8][32];
    static uint32_t last_revisions[8];
    static unsigned next_id;
    for(unsigned i=0;i<8;i++)if(!strcmp(last_ids[i],id)) {
        int same=!strcmp(last_kinds[i],kind);
        emit("{\"type\":\"ack\",\"command_id\":\"%s\",\"applied\":%s,\"revision\":%lu,\"code\":\"%s\"}",
            id,same?"true":"false",(unsigned long)last_revisions[i],same?"IDEMPOTENT":"KEY_CONFLICT");
        return;
    }
    if(field_uint(json,"expected_revision",&expected)&&expected!=revision) {
        emit("{\"type\":\"ack\",\"command_id\":\"%s\",\"applied\":false,\"code\":\"REVISION_CONFLICT\",\"revision\":%lu}",
            id,(unsigned long)revision);
        return;
    }
    int applied=0;
    const char *code="INVALID_STATE";
    if(!strcmp(kind,"start_prepare")&&session_state==0) {
        if(source_ready) {
            prepare_tick=xTaskGetTickCount();
            session_state=1;
            applied=1;
            dsp_reset();
            for(unsigned j=0;j<3;j++) { memset(&windows[j],0,sizeof(Result));windows[j].reason=REASON_PENDING; }
        } else code=input_error();
    } else if(!strcmp(kind,"cancel")&&(session_state==0||session_state==1||session_state==2)) {
        completion_target=5;
        session_state=3;
        applied=1;
    } else if(!strcmp(kind,"mark_end")) {
        code="SOURCE_EVENT_IMMUTABLE";
    } else if(!strcmp(kind,"snapshot")) {
        applied=1;
    }
    if(applied) {
        if(strcmp(kind,"snapshot"))revision++;
        strncpy(last_ids[next_id],id,63);
        strncpy(last_kinds[next_id],kind,31);
        last_revisions[next_id]=revision;
        next_id=(next_id+1)%8;
        code="APPLIED";
    }
    emit("{\"type\":\"ack\",\"command_id\":\"%s\",\"applied\":%s,\"revision\":%lu,\"code\":\"%s\"}",
        id,applied?"true":"false",(unsigned long)revision,code);
}

static void session_task(void *unused) {
    (void)unused;
    char command_buffer[384];
    unsigned length=0;
    int overflow=0;
    Result mailbox;
    for(;;) {
        heartbeat[2]++;
        int ch;
        while((ch=bsp_getc())>=0) {
            if(ch=='\n'||ch=='\r') {
                if(length&&!overflow) { command_buffer[length]=0;command(command_buffer); }
                length=0;
                overflow=0;
            } else if(length+1<sizeof(command_buffer)&&!overflow)command_buffer[length++]=(char)ch;
            else overflow=1;
        }
        (void)xQueueReceive(qSession,&mailbox,0);
        if(session_state==3&&uxQueueMessagesWaiting(qRaw)==0) {
            input_close();
            session_state=completion_target;
            revision++;
            emit("{\"type\":\"event\",\"code\":\"SESSION_TERMINAL\",\"state\":\"%s\"}",states[session_state]);
        }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
static void feedback_task(void *unused) {
    (void)unused;
    Result result;
    for(;;) { (void)xQueueReceive(qFeedback,&result,pdMS_TO_TICKS(100));heartbeat[3]++; }
}
static void ui_task(void *unused) {
    (void)unused;
    Result result;
    for(;;) { (void)xQueueReceive(qUI,&result,pdMS_TO_TICKS(100));heartbeat[4]++; }
}
static void print_result(Result result) {
    char buffer[256];
    if(result.valid) {
        unsigned scaled=(unsigned)lroundf(result.pulse_bpm*1000.f);
        snprintf(buffer,sizeof(buffer),"{\"valid\":true,\"pulse_bpm\":%u.%03u,\"reasons\":[],\"available_us\":%lu,\"start_us\":%lu,\"end_us\":%lu}",
            scaled/1000,scaled%1000,(unsigned long)result.available_us,(unsigned long)result.start_us,(unsigned long)result.end_us);
    } else snprintf(buffer,sizeof(buffer),"{\"valid\":false,\"pulse_bpm\":null,\"reasons\":[\"%s\"],\"available_us\":%lu,\"start_us\":%lu,\"end_us\":%lu}",
            dsp_reason(result.reason),(unsigned long)result.available_us,(unsigned long)result.start_us,(unsigned long)result.end_us);
    for(char *p=buffer;*p;p++)bsp_putc(*p);
}
static void snapshot(void) {
    char buffer[512];
    Result current,results[3];
    taskENTER_CRITICAL();current=latest;memcpy(results,windows,sizeof(results));taskEXIT_CRITICAL();
    snprintf(buffer,sizeof(buffer),"{\"type\":\"snapshot\",\"schema_version\":2,\"execution_mode\":\"FREERTOS_QEMU\",\"origin\":\"PUBLIC_DATA\",\"seq\":%lu,\"revision\":%lu,\"engine_time_us\":%lu,\"session_state\":\"%s\",\"t0_us\":",
        (unsigned long)heartbeat[5],(unsigned long)revision,(unsigned long)processed_us,states[session_state]);
    for(char *p=buffer;*p;p++)bsp_putc(*p);
    if(t0_us==UINT32_MAX) { for(char *p="null";*p;p++)bsp_putc(*p); }
    else {
        snprintf(buffer,sizeof(buffer),"%lu",(unsigned long)t0_us);
        for(char *p=buffer;*p;p++)bsp_putc(*p);
    }
    for(char *p=",\"latest_result\":";*p;p++)bsp_putc(*p);
    print_result(current);
    for(char *p=",\"key_windows\":[";*p;p++)bsp_putc(*p);
    for(unsigned i=0;i<3;i++) { if(i)bsp_putc(',');print_result(results[i]); }
    snprintf(buffer,sizeof(buffer),"],\"diagnostics\":{\"raw_queue\":%lu,\"raw_drops\":%lu,\"telemetry_drops\":0,\"source_rows\":%lu,\"output_rows\":%lu,\"source_crc32\":%lu,\"output_crc32\":%lu,\"sample_hz\":64},\"tasks\":[",
        (unsigned long)uxQueueMessagesWaiting(qRaw),(unsigned long)raw_drops,(unsigned long)input_rows(),
        (unsigned long)output_rows(),(unsigned long)input_crc32(),(unsigned long)output_crc32());
    for(char *p=buffer;*p;p++)bsp_putc(*p);
    for(unsigned i=0;i<6;i++) {
        snprintf(buffer,sizeof(buffer),"%s{\"name\":\"%s\",\"priority\":%u,\"heartbeat\":%lu,\"stack_high_water_words\":%lu,\"state\":%u}",
            i?",":"",task_names[i],6-i,(unsigned long)heartbeat[i],
            (unsigned long)uxTaskGetStackHighWaterMark(handles[i]),(unsigned)eTaskGetState(handles[i]));
        for(char *p=buffer;*p;p++)bsp_putc(*p);
    }
    line_out("]}");
}
static void system_task(void *unused) {
    (void)unused;
    char event[EVENT_LENGTH],hello[320];
    unsigned iteration=0;
    snprintf(hello,sizeof(hello),"{\"type\":\"hello\",\"protocol_version\":2,\"kernel\":\"FreeRTOS V11.2.0\",\"target\":\"mps2-an385\",\"origin\":\"PUBLIC_DATA\",\"sample_hz\":64,\"source_available\":%s,\"source_error\":\"%s\"}",
        source_ready?"true":"false",input_error());
    line_out(hello);
    for(;;) {
        heartbeat[5]++;
        while(xQueueReceive(qEvents,event,0)==pdPASS)line_out(event);
        if(iteration++%10==0)snapshot();
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

void vApplicationGetIdleTaskMemory(StaticTask_t **tcb,StackType_t **stack,uint32_t *size) {
    static StaticTask_t idle;
    static StackType_t idle_stack[256];
    *tcb=&idle;
    *stack=idle_stack;
    *size=256;
}
void vApplicationIdleHook(void) { __asm volatile("wfi"); }
void vApplicationStackOverflowHook(TaskHandle_t task,char *name) {
    (void)task;(void)name;
    taskDISABLE_INTERRUPTS();
    line_out("{\"type\":\"fatal\",\"code\":\"STACK_OVERFLOW\"}");
    for(;;){}
}
void repulse_assert(const char *file,int line) {
    (void)file;(void)line;
    __asm volatile("cpsid i");
    line_out("{\"type\":\"fatal\",\"code\":\"KERNEL_ASSERT\"}");
    for(;;){}
}
int main(void) {
    bsp_init();
    source_ready=input_open();
    latest.reason=REASON_INSUFFICIENT;
    for(unsigned j=0;j<3;j++)windows[j].reason=REASON_PENDING;
    qRaw=xQueueCreateStatic(8,sizeof(SampleBlock),raw_storage,&raw_control);
    qUI=xQueueCreateStatic(1,sizeof(Result),ui_storage,&ui_control);
    qSession=xQueueCreateStatic(1,sizeof(Result),session_storage,&session_control);
    qFeedback=xQueueCreateStatic(1,sizeof(Result),feedback_storage,&feedback_control);
    qEvents=xQueueCreateStatic(16,EVENT_LENGTH,event_storage,&event_control);
    vQueueAddToRegistry(qRaw,"qRaw");vQueueAddToRegistry(qUI,"qUI");
    vQueueAddToRegistry(qSession,"qSession");vQueueAddToRegistry(qFeedback,"qFeedback");
    vQueueAddToRegistry(qEvents,"qEvents");
    handles[0]=xTaskCreateStatic(acquisition_task,"Acquisition",512,0,6,acquisition_stack,&task_tcb[0]);
    handles[1]=xTaskCreateStatic(dsp_task,"DSP",1536,0,5,dsp_stack,&task_tcb[1]);
    handles[2]=xTaskCreateStatic(session_task,"Session",768,0,4,session_stack,&task_tcb[2]);
    handles[3]=xTaskCreateStatic(feedback_task,"Feedback",512,0,3,feedback_stack,&task_tcb[3]);
    handles[4]=xTaskCreateStatic(ui_task,"UI",768,0,2,ui_stack,&task_tcb[4]);
    handles[5]=xTaskCreateStatic(system_task,"System",1024,0,1,system_stack,&task_tcb[5]);
    vTaskStartScheduler();
    for(;;){}
}
