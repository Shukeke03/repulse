#include "dsp.h"
#include <math.h>
#include <string.h>
/* scipy.signal.butter(2,[0.5,5],btype="bandpass",fs=100,output="sos").
 * Coefficients are frozen alongside Python's processor, updated by build verification. */
static const float sos[2][6] = {
 {0.01658193166931919f,0.03316386333863838f,0.01658193166931919f,1.f,-1.6288507685741764f,0.6994634776474277f},
 {1.f,-2.f,1.f,1.f,-1.9573890395427322f,0.9585316851827481f}
};
typedef struct { Sample s; float filtered; uint8_t warm; } Entry;
static Entry ring[DSP_CAPACITY];
static unsigned head,count;
static uint16_t segment;
static uint32_t segment_start,last_us;
static float state[2][2];
void dsp_reset(void){head=count=0;segment=0;segment_start=last_us=0;memset(state,0,sizeof(state));}
void dsp_feed(const Sample *s){
 Entry *e=&ring[head]; e->s=*s;e->warm=1;e->filtered=0;
 if(s->on&&!s->gap){
  if(s->segment!=segment || (last_us&&s->time_us-last_us!=10000)){
   segment=s->segment;segment_start=s->time_us;
   float dc=s->ppg;
   for(unsigned j=0;j<2;j++){float out=dc*(sos[j][0]+sos[j][1]+sos[j][2])/(1+sos[j][4]+sos[j][5]);state[j][0]=out-sos[j][0]*dc;state[j][1]=sos[j][2]*dc-sos[j][5]*out;dc=out;}
  }
  float x=s->ppg;
  for(unsigned j=0;j<2;j++){float y=sos[j][0]*x+state[j][0];state[j][0]=sos[j][1]*x-sos[j][4]*y+state[j][1];state[j][1]=sos[j][2]*x-sos[j][5]*y;x=y;}
  e->filtered=x;e->warm=(s->time_us-segment_start<2000000);last_us=s->time_us;
 }else last_us=0;
 head=(head+1)%DSP_CAPACITY;if(count<DSP_CAPACITY)count++;
}
float dsp_last_filtered(void){return ring[(head+DSP_CAPACITY-1)%DSP_CAPACITY].filtered;}
static float median(float *a,unsigned n){for(unsigned i=1;i<n;i++){float v=a[i];unsigned j=i;while(j&&a[j-1]>v){a[j]=a[j-1];j--;}a[j]=v;}return n%2?a[n/2]:(a[n/2-1]+a[n/2])*0.5f;}
Result dsp_estimate(uint32_t start,uint32_t end,uint32_t available){
 Result r={.available_us=available,.start_us=start,.end_us=end,.valid=0,.reason=REASON_INSUFFICIENT,.pulse_bpm=0};
 float y[800],raw_min=1e20f,raw_max=-1e20f,sum=0,energy=0,rawsum=0,rawsq=0;unsigned n=0,motion_count=0,saturated=0;uint32_t prev=0;uint16_t seg=0;
 for(unsigned i=0;i<count;i++){
  Entry *e=&ring[(head+DSP_CAPACITY-count+i)%DSP_CAPACITY];if(e->s.time_us<start||e->s.time_us>=end)continue;
  if(!e->s.on||e->s.gap){r.reason=REASON_GAP;return r;}
  if(e->warm){r.reason=REASON_WARMUP;return r;}
  if((prev&&e->s.time_us-prev!=10000)||(seg&&seg!=e->s.segment)){r.reason=REASON_GAP;return r;}
  if(n>=800)break;y[n++]=e->filtered;rawsum+=e->s.ppg;rawsq+=e->s.ppg*e->s.ppg;if(e->s.accel>.65f)motion_count++;if(e->s.ppg<=0||e->s.ppg>=262143)saturated++;prev=e->s.time_us;seg=e->s.segment;
  if(e->s.ppg<raw_min)raw_min=e->s.ppg;if(e->s.ppg>raw_max)raw_max=e->s.ppg;
 }
 if(n!=800)return r;if(raw_max-raw_min<1.f){r.reason=REASON_NO_CONTACT;return r;}
 if(rawsq/n-(rawsum/n)*(rawsum/n)<625.f||motion_count>80||saturated>8){r.reason=REASON_POOR_SIGNAL;return r;}
 for(unsigned i=0;i<n;i++)sum+=y[i];sum/=n;
 for(unsigned i=0;i<n;i++){y[i]-=sum;energy+=y[i]*y[i];}
 if(energy/n<100.f){r.reason=REASON_POOR_SIGNAL;return r;}
 float prominence=.35f*sqrtf(energy/n);
 unsigned peaks[28],np=0;
 for(unsigned i=1;i<n-1;i++)if(y[i]>y[i-1]&&y[i]>=y[i+1]){
  float left=y[i],right=y[i];
  for(int j=(int)i-1;j>=0&&y[j]<=y[i];j--)if(y[j]<left)left=y[j];
  for(unsigned j=i+1;j<n&&y[j]<=y[i];j++)if(y[j]<right)right=y[j];
  if(y[i]-fmaxf(left,right)<prominence)continue;
  if(np&&i-peaks[np-1]<30){if(y[i]>y[peaks[np-1]])peaks[np-1]=i;}
  else if(np<28)peaks[np++]=i;
 }
 if(np<4){r.reason=REASON_INTERVALS;return r;}
 float intervals[27],scratch[27];unsigned ni=0;
 for(unsigned i=1;i<np;i++){float d=(float)(peaks[i]-peaks[i-1]);if(d<30||d>150){r.reason=REASON_INTERVALS;return r;}intervals[ni]=scratch[ni]=d;ni++;}
 float med=median(scratch,ni),deviation=0;
 for(unsigned i=0;i<ni;i++)deviation+=fabsf(intervals[i]-med);deviation/=ni;
 if(deviation/med>0.18f){r.reason=REASON_INTERVALS;return r;}
 float best=-1;unsigned lag_best=0;
 for(unsigned lag=(unsigned)fmaxf(30,.85f*med);lag<=(unsigned)fminf(150,1.15f*med);lag++){
  float cross=0,ea=0,eb=0;for(unsigned i=0;i<n-lag;i++){cross+=y[i]*y[i+lag];ea+=y[i]*y[i];eb+=y[i+lag]*y[i+lag];}
  float corr=cross/sqrtf(ea*eb+1e-20f);
  if(corr>best){best=corr;lag_best=lag;}
 }
 if(best<0.45f||fabsf((float)lag_best-med)/med>0.15f){r.reason=REASON_AUTOCORR;return r;}
 r.pulse_bpm=6000.f/med;if(r.pulse_bpm<40||r.pulse_bpm>200){r.reason=REASON_RANGE;return r;}
 r.valid=1;r.reason=REASON_NONE;return r;
}
const char *dsp_reason(int n){static const char *r[]={"VALID","INSUFFICIENT_HISTORY","DATA_GAP","WARMING","POOR_SIGNAL","INTERVAL_INCONSISTENT","AUTOCORRELATION_MISMATCH","NO_CONTACT","OUT_OF_RANGE","PENDING","ACQUISITION_OFF"};return n>=0&&n<=10?r[n]:"INVALID";}
