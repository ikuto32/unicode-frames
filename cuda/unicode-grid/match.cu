// Exhaustive uint8 L2 search: exact int8 tensor-core products and int32 distances.
#include <cuda_runtime.h>
#include <cublas_v2.h>
#include <stdint.h>
#include <climits>
#include <stdexcept>
#include <string>
#include <algorithm>

#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C"
#endif

static thread_local std::string last_error;
static void check(cudaError_t status) { if(status!=cudaSuccess) throw std::runtime_error(cudaGetErrorString(status)); }
static void blas(cublasStatus_t status) { if(status!=CUBLAS_STATUS_SUCCESS) throw std::runtime_error("cuBLAS status "+std::to_string(status)); }
template<class T> struct Device {
    T* ptr=nullptr;
    explicit Device(size_t size) { check(cudaMalloc((void**)&ptr,size*sizeof(T))); }
    ~Device() { if(ptr)cudaFree(ptr); }
};
struct Handle {
    cublasHandle_t handle=nullptr;
    Handle() { blas(cublasCreate(&handle)); }
    ~Handle() { if(handle)cublasDestroy(handle); }
};

__global__ void center(uint8_t* data,size_t n) {
    size_t i=size_t(blockIdx.x)*blockDim.x+threadIdx.x;
    if(i<n)data[i]^=128;
}
__global__ void norms(const int8_t* vectors,int n,int d,int32_t* out) {
    int row=blockIdx.x;
    if(row>=n)return;
    int sum=0;
    for(int i=threadIdx.x;i<d;i+=blockDim.x) { int v=vectors[size_t(row)*d+i];sum+=v*v; }
    __shared__ int partial[256];partial[threadIdx.x]=sum;__syncthreads();
    for(int step=128;step;step/=2) { if(threadIdx.x<step)partial[threadIdx.x]+=partial[threadIdx.x+step];__syncthreads(); }
    if(!threadIdx.x)out[row]=partial[0];
}
__global__ void initialize(int32_t* errors,int32_t* ids,int n) {
    int i=blockIdx.x*blockDim.x+threadIdx.x;
    if(i<n){errors[i]=INT_MAX;ids[i]=INT_MAX;}
}
__global__ void rank(const int32_t* products,const int32_t* qnorm,const int32_t* bnorm,
                     int actual_bank,int leading,int qoffset,int boffset,int32_t* errors,int32_t* ids) {
    int q=blockIdx.x,lane=threadIdx.x;
    int best=INT_MAX,id=INT_MAX;
    for(int i=lane;i<actual_bank;i+=blockDim.x) {
        int distance=qnorm[qoffset+q]+bnorm[boffset+i]-2*products[size_t(q)*leading+i];
        int candidate=boffset+i;
        if(distance<best||(distance==best&&candidate<id)){best=distance;id=candidate;}
    }
    __shared__ int best_values[256],best_ids[256];
    best_values[lane]=best;best_ids[lane]=id;__syncthreads();
    for(int step=128;step;step/=2) {
        if(lane<step) {
            int v=best_values[lane+step],j=best_ids[lane+step];
            if(v<best_values[lane]||(v==best_values[lane]&&j<best_ids[lane])){best_values[lane]=v;best_ids[lane]=j;}
        }
        __syncthreads();
    }
    if(!lane) {
        int at=qoffset+q,v=best_values[0],j=best_ids[0];
        if(v<errors[at]||(v==errors[at]&&j<ids[at])){errors[at]=v;ids[at]=j;}
    }
}

API const char* unicode_match_error(){return last_error.c_str();}
API int unicode_match_u8(const uint8_t* queries,int nq,const uint8_t* bank,int nb,int d,
                         int qbatch,int bbatch,int32_t* ids,int32_t* errors) {
    try {
        if(!queries||!bank||!ids||!errors||nq<1||nb<1||d<1||d%4||d>32000||qbatch<4||bbatch<4)
            throw std::runtime_error("Invalid integer matching dimensions");
        check(cudaSetDevice(0));Handle handle;
        qbatch=(qbatch+3)/4*4;bbatch=(bbatch+3)/4*4;
        const int qp=(nq+3)/4*4,bp=(nb+3)/4*4;
        Device<int8_t> q(size_t(qp)*d),b(size_t(bp)*d);
        Device<int32_t> qn(qp),bn(bp),best(nq),best_ids(nq),products(size_t(qbatch)*bbatch);
        check(cudaMemset(q.ptr,0,size_t(qp)*d));check(cudaMemset(b.ptr,0,size_t(bp)*d));
        check(cudaMemcpy(q.ptr,queries,size_t(nq)*d,cudaMemcpyHostToDevice));
        check(cudaMemcpy(b.ptr,bank,size_t(nb)*d,cudaMemcpyHostToDevice));
        center<<<unsigned((size_t(qp)*d+255)/256),256>>>((uint8_t*)q.ptr,size_t(qp)*d);
        center<<<unsigned((size_t(bp)*d+255)/256),256>>>((uint8_t*)b.ptr,size_t(bp)*d);
        norms<<<qp,256>>>(q.ptr,qp,d,qn.ptr);norms<<<bp,256>>>(b.ptr,bp,d,bn.ptr);
        initialize<<<(nq+255)/256,256>>>(best.ptr,best_ids.ptr,nq);check(cudaGetLastError());
        const int32_t alpha=1,beta=0;
        for(int qs=0;qs<nq;qs+=qbatch) {
            int count=std::min(qbatch,nq-qs),padded_q=(count+3)/4*4;
            for(int bs=0;bs<nb;bs+=bbatch) {
                int actual=std::min(bbatch,nb-bs),padded_b=(actual+3)/4*4;
                blas(cublasGemmEx(handle.handle,CUBLAS_OP_T,CUBLAS_OP_N,padded_b,padded_q,d,&alpha,
                    b.ptr+size_t(bs)*d,CUDA_R_8I,d,q.ptr+size_t(qs)*d,CUDA_R_8I,d,&beta,
                    products.ptr,CUDA_R_32I,padded_b,CUBLAS_COMPUTE_32I,CUBLAS_GEMM_DEFAULT_TENSOR_OP));
                rank<<<count,256>>>(products.ptr,qn.ptr,bn.ptr,actual,padded_b,qs,bs,best.ptr,best_ids.ptr);
                check(cudaGetLastError());
            }
        }
        check(cudaMemcpy(errors,best.ptr,size_t(nq)*sizeof(int32_t),cudaMemcpyDeviceToHost));
        check(cudaMemcpy(ids,best_ids.ptr,size_t(nq)*sizeof(int32_t),cudaMemcpyDeviceToHost));
        check(cudaDeviceSynchronize());last_error.clear();return 0;
    } catch(const std::exception& error) { last_error=error.what();return -1; }
}
