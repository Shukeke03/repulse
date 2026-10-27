#include "dsp.h"
#include <math.h>
#include <string.h>

/* scipy.signal.butter(2,[0.5,5],btype="bandpass",fs=64,output="sos").
 * Input remains the native signed Empatica BVP, expressed in arbitrary units. */
static const float sos[2][6] = {
    {0.0368612103833f,0.0737224207666f,0.0368612103833f,1.f,-1.41370851224f,0.572405462222f},
    {1.f,-2.f,1.f,1.f,-1.93314414048f,0.935896883185f}
};
typedef struct { Sample sample; float filtered; uint8_t warm; } Entry;
static Entry ring[DSP_CAPACITY];
static unsigned head, count;
static uint16_t segment;
static uint32_t segment_start, last_us, last_seq;
static int continuous;
static float state[2][2];

void dsp_reset(void) {
    head=count=0;
    segment=0;
    segment_start=last_us=last_seq=0;
    continuous=0;
    memset(state,0,sizeof(state));
}
void dsp_feed(const Sample *s) {
    Entry *e=&ring[head];
    e->sample=*s;
    e->warm=1;
    e->filtered=0;
    if(s->on&&!s->gap&&isfinite(s->ppg)&&isfinite(s->accel)) {
        if(!continuous || s->segment!=segment || s->time_us-last_us!=DSP_SAMPLE_US || s->seq-last_seq!=1) {
            segment=s->segment;
            segment_start=s->time_us;
            float dc=s->ppg;
            for(unsigned j=0;j<2;j++) {
                float out=dc*(sos[j][0]+sos[j][1]+sos[j][2])/(1+sos[j][4]+sos[j][5]);
                state[j][0]=out-sos[j][0]*dc;
                state[j][1]=sos[j][2]*dc-sos[j][5]*out;
                dc=out;
            }
        }
        float x=s->ppg;
        for(unsigned j=0;j<2;j++) {
            float y=sos[j][0]*x+state[j][0];
            state[j][0]=sos[j][1]*x-sos[j][4]*y+state[j][1];
            state[j][1]=sos[j][2]*x-sos[j][5]*y;
            x=y;
        }
        e->filtered=x;
        e->warm=(s->time_us-segment_start<2000000);
        last_us=s->time_us;
        last_seq=s->seq;
        continuous=1;
    } else {
        continuous=0;
    }
    head=(head+1)%DSP_CAPACITY;
    if(count<DSP_CAPACITY)count++;
}
float dsp_last_filtered(void) { return ring[(head+DSP_CAPACITY-1)%DSP_CAPACITY].filtered; }
int dsp_last_warming(void) { return ring[(head+DSP_CAPACITY-1)%DSP_CAPACITY].warm; }
static float median(float *a,unsigned n) {
    for(unsigned i=1;i<n;i++) {
        float value=a[i];
        unsigned j=i;
        while(j&&a[j-1]>value) { a[j]=a[j-1]; j--; }
        a[j]=value;
    }
    return n%2?a[n/2]:(a[n/2-1]+a[n/2])*0.5f;
}
Result dsp_estimate(uint32_t start,uint32_t end,uint32_t available) {
    Result r={.available_us=available,.start_us=start,.end_us=end,.reason=REASON_INSUFFICIENT};
    float y[DSP_WINDOW_SAMPLES],raw_min=1e20f,raw_max=-1e20f,sum=0,energy=0,rawsum=0,rawsq=0;
    unsigned n=0,motion_count=0;
    uint32_t previous=0,previous_seq=0;
    uint16_t window_segment=0;
    for(unsigned i=0;i<count;i++) {
        const Entry *e=&ring[(head+DSP_CAPACITY-count+i)%DSP_CAPACITY];
        const Sample *s=&e->sample;
        if(s->time_us<start||s->time_us>=end)continue;
        if(!s->on) { r.reason=REASON_OFF;return r; }
        if(s->gap||!isfinite(s->ppg)||!isfinite(s->accel)) { r.reason=REASON_GAP;return r; }
        if(e->warm) { r.reason=REASON_WARMUP;return r; }
        if((n&&(s->time_us-previous!=DSP_SAMPLE_US||s->seq-previous_seq!=1)) ||
            (window_segment&&window_segment!=s->segment)) { r.reason=REASON_GAP;return r; }
        if(n>=DSP_WINDOW_SAMPLES) { r.reason=REASON_GAP;return r; }
        y[n++]=e->filtered;
        rawsum+=s->ppg;
        rawsq+=s->ppg*s->ppg;
        if(s->accel>.35f)motion_count++;
        previous=s->time_us;
        previous_seq=s->seq;
        window_segment=s->segment;
        if(s->ppg<raw_min)raw_min=s->ppg;
        if(s->ppg>raw_max)raw_max=s->ppg;
    }
    if(n!=DSP_WINDOW_SAMPLES)return r;
    float raw_variance=rawsq/n-(rawsum/n)*(rawsum/n);
    if(raw_max-raw_min<1e-5f||raw_variance<.0025f) { r.reason=REASON_LOW_AMPLITUDE;return r; }
    if(motion_count>DSP_WINDOW_SAMPLES/10) { r.reason=REASON_POOR_SIGNAL;return r; }
    for(unsigned i=0;i<n;i++)sum+=y[i];
    sum/=n;
    for(unsigned i=0;i<n;i++) { y[i]-=sum;energy+=y[i]*y[i]; }
    if(energy/n<.0004f) { r.reason=REASON_LOW_AMPLITUDE;return r; }
    float prominence=.35f*sqrtf(energy/n);
    unsigned peaks[30],np=0;
    for(unsigned i=1;i<n-1;i++)if(y[i]>y[i-1]&&y[i]>=y[i+1]) {
        float left=y[i],right=y[i];
        for(int j=(int)i-1;j>=0&&y[j]<=y[i];j--)if(y[j]<left)left=y[j];
        for(unsigned j=i+1;j<n&&y[j]<=y[i];j++)if(y[j]<right)right=y[j];
        if(y[i]-fmaxf(left,right)<prominence)continue;
        if(np&&i-peaks[np-1]<19) {
            if(y[i]>y[peaks[np-1]])peaks[np-1]=i;
        } else if(np<30)peaks[np++]=i;
    }
    if(np<4) { r.reason=REASON_INTERVALS;return r; }
    float intervals[29],scratch[29],deviations[29];
    unsigned ni=0;
    for(unsigned i=1;i<np;i++) {
        float delta=(float)(peaks[i]-peaks[i-1]);
        if(delta<19.f||delta>96.f) { r.reason=REASON_INTERVALS;return r; }
        intervals[ni]=scratch[ni]=delta;
        ni++;
    }
    float middle=median(scratch,ni),maximum_deviation=0;
    for(unsigned i=0;i<ni;i++) {
        deviations[i]=fabsf(intervals[i]-middle);
        if(deviations[i]>maximum_deviation)maximum_deviation=deviations[i];
    }
    if(median(deviations,ni)/middle>.18f||maximum_deviation/middle>.35f) {
        r.reason=REASON_INTERVALS;return r;
    }
    float best=-1;
    unsigned best_lag=0;
    unsigned lo=(unsigned)fmaxf(19,.85f*middle),hi=(unsigned)fminf(96,1.15f*middle);
    for(unsigned lag=lo;lag<=hi;lag++) {
        float cross=0;
        for(unsigned i=0;i<n-lag;i++)cross+=y[i]*y[i+lag];
        float score=cross/(energy+1e-20f);
        if(score>best) { best=score;best_lag=lag; }
    }
    if(best<.45f||fabsf((float)best_lag-middle)/middle>.15f) { r.reason=REASON_AUTOCORR;return r; }
    r.pulse_bpm=3840.f/middle;
    if(r.pulse_bpm<40||r.pulse_bpm>200) { r.reason=REASON_RANGE;return r; }
    r.valid=1;
    r.reason=REASON_NONE;
    return r;
}
const char *dsp_reason(int reason) {
    static const char *labels[]={"VALID","INSUFFICIENT_HISTORY","DATA_GAP","WARMUP","MOTION_ARTIFACT",
        "IRREGULAR_INTERVALS","AUTOCORRELATION_MISMATCH","LOW_AMPLITUDE","OUT_OF_RANGE","PENDING","ACQUISITION_OFF"};
    return reason>=0&&reason<=10?labels[reason]:"INVALID";
}
