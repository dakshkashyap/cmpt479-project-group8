# CSIL x86-64 toolchain

```
host=csil-cpu10
uname=Linux csil-cpu10 6.17.0-35-generic #35~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC Tue May 26 19:30:42 UTC 2 x86_64 x86_64 x86_64 GNU/Linux
arch=x86_64
os=Ubuntu 24.04.4 LTS
cpu_model=QEMU Virtual CPU version 2.5+
hypervisor=KVM
cpu_flags_simd=sse sse2 ssse3 sse4_1 sse4_2
note_cpu=Not a physical Ice Lake CPU; QEMU guest exposes SSE4.2 only (no AVX/AVX2/AVX512).
cpus=8
ram_gib=31
compiler=c++ (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
compiler_path=/usr/bin/c++
llvm=18.1.3
llvm_dir=/usr/lib/llvm-18/lib/cmake/llvm
llvm_note=LLVM 16 preferred by project docs; CSIL uses system LLVM 18.
parabix_commit=f0369dd138e2e7a710566d5035f68b9cdc0bf305
simdutf_tag=v9.0.0
simdutf_commit=ca7acbcea967b5dcbab490066e99e3a6e6925539
simdutf_impl=westmere
build_type=Release
bench_label=utf16_benchmark_csil_x86_64
bench_datasets=all (default + multilingual modes)
bench_sizes_mb=128,256,512
bench_warmups=2
bench_repetitions=7
bench_include_simdutf=1
timed_rows=126
result_ok_all=true
summary=results/utf16_benchmark_csil_x86_64_summary.md
```
