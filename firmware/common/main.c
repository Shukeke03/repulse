#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "bsp.h"
#include "dsp.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>

#define PI 3.14159265358979323846f
#define EVENT_LENGTH 256
static StaticQueue_t raw_control,ui_control,session_control,feedback_control,event_control,telemetry_control;
static uint8_t raw_storage[8*sizeof(SampleBlock)],ui_storage[sizeof(Result)],session_storage[sizeof(Result)],feedback_storage[sizeof(Result)];
static uint8_t event_storage[16*EVENT_LENGTH],telemetry_storage[16*sizeof(SampleBlock)];
static QueueHandle_t qRaw,qUI,qSession,qFeedback,qEvents,qTelemetry;
static StaticTask_t task_tcb[6];
static StackType_t acquisition_stack[512],dsp_stack[1536],session_stack[768],feedback_stack[512],ui_stack[768],system_stack[1024];
static TaskHandle_t handles[6];
static volatile uint32_t heartbeat[6],revision=0,raw_drops=0,telemetry_drops=0,sample_sequence=0,boot_tick,command_seq=0;
static volatile int session_state=0,strategy=0;
#ifdef REPULSE_QEMU
static uint32_t source_seed=20261025;
#endif
static volatile uint32_t t0_us=UINT32_MAX,prepare_tick=0;
static volatile uint32_t ending_us=0;
static Result latest,windows[3];
static const char *states[]={"CREATED","PREPARING","RECOVERING","FINALIZING","COMPLETED","CANCELLED","FAILED"};
static const char *task_names[]={"Acquisition","DSP","Session","Feedback","UI","System"};
static uint32_t engine_time(void){return (uint32_t)(xTaskGetTickCount()-prepare_tick)*1000;}
static void line_out(const char *s){while(*s)bsp_putc(*s++);bsp_putc('\n');}
static void emit(const char *fmt,...){char msg[EVENT_LENGTH];va_list args;va_start(args,fmt);vsnprintf(msg,sizeof(msg),fmt,args);va_end(args);if(xQueueSend(qEvents,msg,0)!=pdPASS){session_state=6;raw_drops++;}}
static int acquisition_enabled(uint32_t time){
 if(session_state==1)return 1;if(session_state!=2)return 0;
 float rel=((int64_t)time-(int64_t)t0_us)/1000000.f;
 if(rel<0)return 1;
 if(strategy==1)return rel>=0&&rel<124;
 if(strategy==2)return rel>=0&&rel<124&&fmodf(rel,20.f)<11.29f;
 const float ranges[7][2]={{0,10},{20,30},{40,50},{54,64},{80,90},{100,110},{114,124}};
 for(unsigned i=0;i<7;i++)if(rel>=ranges[i][0]&&rel<ranges[i][1])return 1;return 0;
}
#ifdef REPULSE_QEMU
static double source_noise(uint32_t index){
 uint64_t z=(uint64_t)index+(uint64_t)source_seed;
 z=(z^(z>>16))*0x45D9F3BULL;z=(z^(z>>16))*0x45D9F3BULL;
 double u=(double)((z^(z>>16))&0xFFFFFFFFULL)/4294967296.0;
 uint64_t w=z*1664525ULL+1013904223ULL;double v=(double)(w&0xFFFFFFFFULL)/4294967296.0;
 return sqrt(-2.0*log(fmax(u,1e-12)))*cos(6.2831853071795864769*v);
}
#endif
static void acquisition_task(void *unused){(void)unused;TickType_t wake=xTaskGetTickCount();uint32_t cursor=0;uint16_t segment=0;int was_on=0;
 for(;;){vTaskDelayUntil(&wake,pdMS_TO_TICKS(100));heartbeat[0]++;if(session_state!=1&&session_state!=2){cursor=0;was_on=0;continue;}
  SampleBlock block={.count=10};
  for(unsigned i=0;i<10;i++){
   uint32_t time=cursor;cursor+=10000;int on=acquisition_enabled(time);if(on&&!was_on)segment++;was_on=on;
   Sample s={.time_us=time,.seq=sample_sequence++,.segment=segment,.on=(uint8_t)on,.gap=0,.ppg=0,.accel=0.03f};
#ifdef REPULSE_QEMU
   /* Synthetic observations are generated before DSP. No truth fields enter DSP. */
   if(on){double seconds=(double)time/1000000.0;
    double marker=t0_us==UINT32_MAX?seconds:(double)t0_us/1000000.0;
    double elapsed=fmax(seconds-marker,0),before=fmin(seconds,marker);
    double phase=6.2831853071795864769/60.0*(150.0*before+72.0*elapsed+78.0*48.0*(-expm1(-elapsed/48.0)));
    s.ppg=(float)(60000.0+4500.0*(sin(phase)+.22*sin(2*phase+.35))+150*sin(6.2831853071795864769*.15*seconds)+55*source_noise(time/10000));
    s.accel=(float)(.025+.008*sin(6.2831853071795864769*.7*seconds));
   }
#else
   if(on&&!bsp_read_observation(&s.ppg,&s.accel)){s.on=0;s.gap=1;}
#endif
   block.values[i]=s;
  }
  if(xQueueSend(qRaw,&block,pdMS_TO_TICKS(20))!=pdPASS){raw_drops++;session_state=6;emit("{\"type\":\"event\",\"code\":\"RAW_QUEUE_OVERFLOW\"}");}
 }
}
static void dsp_task(void *unused){(void)unused;SampleBlock b;uint32_t last_estimate=0;
 for(;;){if(xQueueReceive(qRaw,&b,portMAX_DELAY)!=pdPASS)continue;heartbeat[1]++;
  for(unsigned i=0;i<b.count;i++){dsp_feed(&b.values[i]);b.filtered[i]=dsp_last_filtered();}
  uint32_t end=b.values[b.count-1].time_us+10000;
  if(end-last_estimate>=1000000){last_estimate=end;Result r;if(end<8000000){memset(&r,0,sizeof(r));r.reason=REASON_INSUFFICIENT;r.end_us=end;}else r=dsp_estimate(end-8000000,end,end);
   if(!b.values[b.count-1].on){r.valid=0;r.reason=REASON_GAP;}
   taskENTER_CRITICAL();latest=r;taskEXIT_CRITICAL();xQueueOverwrite(qUI,&r);xQueueOverwrite(qSession,&r);xQueueOverwrite(qFeedback,&r);
  }
  static uint16_t current_segment=0;static uint32_t segment_begin=0;
  Sample *last=&b.values[b.count-1];
  if(last->segment!=current_segment){current_segment=last->segment;segment_begin=b.values[0].time_us;}
  if(!last->on||last->gap||last->time_us-segment_begin<2000000){taskENTER_CRITICAL();latest.valid=0;latest.reason=!last->on?10:last->gap?REASON_GAP:REASON_WARMUP;latest.end_us=end;taskEXIT_CRITICAL();}
  if(t0_us!=UINT32_MAX){static const uint32_t center[3]={0,60000000,120000000};for(unsigned j=0;j<3;j++)if(windows[j].reason==REASON_PENDING&&end>=t0_us+center[j]+4000000){Result r;
    if(t0_us+center[j]<6000000){memset(&r,0,sizeof(r));r.reason=REASON_INSUFFICIENT;r.available_us=end;r.end_us=t0_us+center[j]+4000000;}
    else r=dsp_estimate(t0_us+center[j]-4000000,t0_us+center[j]+4000000,end);
    taskENTER_CRITICAL();windows[j]=r;taskEXIT_CRITICAL();
   }}
  if(xQueueSend(qTelemetry,&b,0)!=pdPASS){telemetry_drops++;session_state=6;}
  if(session_state==2&&t0_us!=UINT32_MAX&&end>=t0_us+124000000){ending_us=end;session_state=3;revision++;}
 }
}
static int field_string(const char *json,const char *key,char *out,unsigned capacity){char token[80];snprintf(token,sizeof(token),"\"%s\"",key);char *p=strstr(json,token);if(!p)return 0;p+=strlen(token);while(*p==' '||*p=='\t')p++;if(*p++!=':')return 0;while(*p==' '||*p=='\t')p++;if(*p++!='\"')return 0;unsigned n=0;while(*p&&*p!='\"'){if((unsigned char)*p<32||*p=='\\'||n+1>=capacity)return 0;out[n++]=*p++;}if(*p!='\"')return 0;out[n]=0;return 1;}
static int field_uint(const char *json,const char *key,uint32_t *out){char token[80];snprintf(token,sizeof(token),"\"%s\"",key);char *p=strstr(json,token);if(!p)return 0;p+=strlen(token);while(*p==' ')p++;if(*p++!=':')return 0;while(*p==' ')p++;if(*p<'0'||*p>'9')return 0;*out=(uint32_t)strtoul(p,0,10);return 1;}
static void command(const char *json){
 char kind[32],id[64];uint32_t expected=revision;
 if(!field_string(json,"type",kind,sizeof(kind))||!field_string(json,"command_id",id,sizeof(id))){emit("{\"type\":\"ack\",\"applied\":false,\"code\":\"INVALID_PARAMETER\"}");return;}
 static char last_ids[8][64],last_kinds[8][32];static uint32_t last_revisions[8];static unsigned next_id;
 for(unsigned i=0;i<8;i++)if(!strcmp(last_ids[i],id)){
  emit("{\"type\":\"ack\",\"command_id\":\"%s\",\"applied\":%s,\"revision\":%lu,\"code\":\"%s\"}",id,!strcmp(last_kinds[i],kind)?"true":"false",(unsigned long)last_revisions[i],!strcmp(last_kinds[i],kind)?"IDEMPOTENT":"KEY_CONFLICT");return;
 }
 if(field_uint(json,"expected_revision",&expected)&&expected!=revision){emit("{\"type\":\"ack\",\"command_id\":\"%s\",\"applied\":false,\"code\":\"REVISION_CONFLICT\",\"revision\":%lu}",id,(unsigned long)revision);return;}
 int applied=0;const char *code="INVALID_STATE";
 if(!strcmp(kind,"start_prepare")&&session_state==0){
#ifdef REPULSE_QEMU
  field_uint(json,"seed",&source_seed);char policy[24];strategy=0;if(field_string(json,"strategy",policy,sizeof(policy))){if(!strcmp(policy,"continuous"))strategy=1;else if(!strcmp(policy,"periodic"))strategy=2;}
  prepare_tick=xTaskGetTickCount();session_state=1;applied=1;dsp_reset();for(unsigned j=0;j<3;j++){memset(&windows[j],0,sizeof(Result));windows[j].reason=REASON_PENDING;}
#else
  code="SOURCE_UNAVAILABLE";
#endif
 }else if(!strcmp(kind,"mark_end")&&session_state==1){t0_us=engine_time();session_state=2;applied=1;
 }else if(!strcmp(kind,"cancel")&&(session_state==1||session_state==2)){ending_us=engine_time();session_state=3;applied=1;
 }else if(!strcmp(kind,"snapshot")){applied=1;}
 if(applied){if(strcmp(kind,"snapshot"))revision++;code="APPLIED";strncpy(last_ids[next_id],id,63);strncpy(last_kinds[next_id],kind,31);last_revisions[next_id]=revision;next_id=(next_id+1)%8;command_seq++;}
 emit("{\"type\":\"ack\",\"command_id\":\"%s\",\"applied\":%s,\"code\":\"%s\",\"revision\":%lu,\"engine_time_us\":%lu}",id,applied?"true":"false",code,(unsigned long)revision,(unsigned long)engine_time());
}
static void session_task(void *unused){(void)unused;char command_buffer[384];unsigned n=0;Result mailbox;int overflow=0;
 for(;;){heartbeat[2]++;int ch;while((ch=bsp_getc())>=0){if(ch=='\n'||ch=='\r'){if(n&&!overflow){command_buffer[n]=0;command(command_buffer);}n=0;overflow=0;}else if(n+1<sizeof(command_buffer)&&!overflow)command_buffer[n++]=(char)ch;else overflow=1;}
  xQueueReceive(qSession,&mailbox,0);
  if(session_state==2&&engine_time()-t0_us>=125000000){ending_us=engine_time();session_state=3;revision++;emit("{\"type\":\"event\",\"code\":\"FINALIZE_TIMEOUT\"}");}
  if(session_state==3&&uxQueueMessagesWaiting(qRaw)==0){session_state=(t0_us!=UINT32_MAX&&ending_us-t0_us>=124000000)?4:5;revision++;emit("{\"type\":\"event\",\"code\":\"SESSION_TERMINAL\",\"state\":\"%s\"}",states[session_state]);}
  if(session_state==1&&engine_time()>=1800000000){ending_us=engine_time();session_state=3;revision++;emit("{\"type\":\"event\",\"code\":\"PREPARE_TIMEOUT\"}");}
  /* QEMU CMSDK UART RX has one-byte storage; 1ms transport polling prevents
   * JSON commands taking one 20ms business period per byte. */
  vTaskDelay(pdMS_TO_TICKS(1));
 }
}
static void feedback_task(void *unused){(void)unused;Result r;for(;;){xQueueReceive(qFeedback,&r,pdMS_TO_TICKS(100));heartbeat[3]++;}}
static void ui_task(void *unused){(void)unused;Result r;for(;;){xQueueReceive(qUI,&r,pdMS_TO_TICKS(100));heartbeat[4]++;}}
static void print_result(Result r){char buf[240];if(r.valid)snprintf(buf,sizeof(buf),"{\"valid\":true,\"pulse_bpm\":%lu,\"reasons\":[],\"available_us\":%lu,\"start_us\":%lu,\"end_us\":%lu}",(unsigned long)lroundf(r.pulse_bpm),(unsigned long)r.available_us,(unsigned long)r.start_us,(unsigned long)r.end_us);else snprintf(buf,sizeof(buf),"{\"valid\":false,\"pulse_bpm\":null,\"reasons\":[\"%s\"],\"available_us\":%lu,\"start_us\":%lu,\"end_us\":%lu}",dsp_reason(r.reason),(unsigned long)r.available_us,(unsigned long)r.start_us,(unsigned long)r.end_us);for(char *p=buf;*p;p++)bsp_putc(*p);}
static void snapshot(void){char buf[400];Result cur,win[3];taskENTER_CRITICAL();cur=latest;memcpy(win,windows,sizeof(win));taskEXIT_CRITICAL();
 snprintf(buf,sizeof(buf),"{\"type\":\"snapshot\",\"schema_version\":1,\"execution_mode\":\"FREERTOS_QEMU\",\"origin\":\"SIM\",\"seq\":%lu,\"revision\":%lu,\"engine_time_us\":%lu,\"session_state\":\"%s\",\"t0_us\":",(unsigned long)heartbeat[5],(unsigned long)revision,(unsigned long)engine_time(),states[session_state]);for(char *p=buf;*p;p++)bsp_putc(*p);
 if(t0_us==UINT32_MAX){for(char *p="null";*p;p++)bsp_putc(*p);}else{snprintf(buf,sizeof(buf),"%lu",(unsigned long)t0_us);for(char *p=buf;*p;p++)bsp_putc(*p);}
 for(char *p=",\"latest_result\":";*p;p++)bsp_putc(*p);print_result(cur);for(char *p=",\"key_windows\":[";*p;p++)bsp_putc(*p);
 for(unsigned i=0;i<3;i++){if(i)bsp_putc(',');print_result(win[i]);}
 snprintf(buf,sizeof(buf),"],\"diagnostics\":{\"raw_queue\":%lu,\"raw_drops\":%lu,\"telemetry_drops\":%lu,\"sample_seq\":%lu},\"tasks\":[",(unsigned long)uxQueueMessagesWaiting(qRaw),(unsigned long)raw_drops,(unsigned long)telemetry_drops,(unsigned long)sample_sequence);for(char *p=buf;*p;p++)bsp_putc(*p);
 for(unsigned i=0;i<6;i++){snprintf(buf,sizeof(buf),"%s{\"name\":\"%s\",\"priority\":%u,\"heartbeat\":%lu,\"stack_high_water_words\":%lu,\"state\":%u}",i?",":"",task_names[i],6-i,(unsigned long)heartbeat[i],(unsigned long)uxTaskGetStackHighWaterMark(handles[i]),(unsigned)eTaskGetState(handles[i]));for(char *p=buf;*p;p++)bsp_putc(*p);}
 line_out("]}");
}
static void system_task(void *unused){(void)unused;char event[EVENT_LENGTH],buf[160];SampleBlock b;unsigned iteration=0;
 line_out("{\"type\":\"hello\",\"protocol_version\":1,\"kernel\":\"FreeRTOS V11.2.0\",\"target\":\"mps2-an385\",\"origin\":\"SIM\",\"sensor_available\":false,\"dsp_parity_verified\":false}");
 for(;;){heartbeat[5]++;while(xQueueReceive(qEvents,event,0)==pdPASS)line_out(event);
  while(xQueueReceive(qTelemetry,&b,0)==pdPASS){for(char *p="{\"type\":\"samples\",\"samples\":[";*p;p++)bsp_putc(*p);for(unsigned i=0;i<b.count;i++){Sample *s=&b.values[i];snprintf(buf,sizeof(buf),"%s[%lu,%lu,%u,%ld,%ld,%u,%u,%ld]",i?",":"",(unsigned long)s->time_us,(unsigned long)s->seq,s->segment,(long)lroundf(s->ppg*1000.f),(long)lroundf(s->accel*1000.f),s->on,s->gap,(long)lroundf(b.filtered[i]*1000.f));for(char *p=buf;*p;p++)bsp_putc(*p);}line_out("]}");}
  if(iteration++%5==0)snapshot();vTaskDelay(pdMS_TO_TICKS(20));
 }
}
void vApplicationGetIdleTaskMemory(StaticTask_t **tcb,StackType_t **stack,uint32_t *size){static StaticTask_t idle;static StackType_t idle_stack[256];*tcb=&idle;*stack=idle_stack;*size=256;}
void vApplicationIdleHook(void){__asm volatile("wfi");}
void vApplicationStackOverflowHook(TaskHandle_t task,char *name){(void)task;(void)name;taskDISABLE_INTERRUPTS();line_out("{\"type\":\"fatal\",\"code\":\"STACK_OVERFLOW\"}");for(;;){}}
void repulse_assert(const char *file,int line){(void)file;(void)line;__asm volatile("cpsid i");line_out("{\"type\":\"fatal\",\"code\":\"KERNEL_ASSERT\"}");for(;;){}}
int main(void){bsp_init();latest.reason=REASON_INSUFFICIENT;for(unsigned j=0;j<3;j++)windows[j].reason=REASON_PENDING;boot_tick=0;(void)boot_tick;
 qRaw=xQueueCreateStatic(8,sizeof(SampleBlock),raw_storage,&raw_control);qUI=xQueueCreateStatic(1,sizeof(Result),ui_storage,&ui_control);qSession=xQueueCreateStatic(1,sizeof(Result),session_storage,&session_control);qFeedback=xQueueCreateStatic(1,sizeof(Result),feedback_storage,&feedback_control);qEvents=xQueueCreateStatic(16,EVENT_LENGTH,event_storage,&event_control);qTelemetry=xQueueCreateStatic(16,sizeof(SampleBlock),telemetry_storage,&telemetry_control);
 vQueueAddToRegistry(qRaw,"qRaw");vQueueAddToRegistry(qUI,"qUI");vQueueAddToRegistry(qSession,"qSession");vQueueAddToRegistry(qFeedback,"qFeedback");vQueueAddToRegistry(qEvents,"qEvents");
 handles[0]=xTaskCreateStatic(acquisition_task,"Acquisition",512,0,6,acquisition_stack,&task_tcb[0]);handles[1]=xTaskCreateStatic(dsp_task,"DSP",1536,0,5,dsp_stack,&task_tcb[1]);handles[2]=xTaskCreateStatic(session_task,"Session",768,0,4,session_stack,&task_tcb[2]);handles[3]=xTaskCreateStatic(feedback_task,"Feedback",512,0,3,feedback_stack,&task_tcb[3]);handles[4]=xTaskCreateStatic(ui_task,"UI",768,0,2,ui_stack,&task_tcb[4]);handles[5]=xTaskCreateStatic(system_task,"System",1024,0,1,system_stack,&task_tcb[5]);vTaskStartScheduler();for(;;){}
}
