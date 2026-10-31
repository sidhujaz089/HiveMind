#!/bin/sh
# Android build environment for the GX10 (aarch64). Source it, do not run it:
#
#     . scripts/android-env.sh && cd projects/superbutton && ./gradlew assembleDebug
#
# WHY THIS FILE EXISTS
# --------------------
# Google ships no aarch64 build-tools for Linux, at any version (L-079). aapt2, aapt and
# zipalign are x86-64 only, and aapt2 compiles resources on every build, so nothing gets
# past it natively.
#
# THE SHAPE THAT WORKS IS *LESS* EMULATION, NOT MORE (L-080)
# ---------------------------------------------------------
# The obvious route -- an x86_64 container with the whole toolchain -- does not merely run
# slowly, it CRASHES: emulating a JVM under qemu-user gives "QEMU internal SIGSEGV",
# because JIT, threading and signal handling are exactly what user-mode emulation handles
# worst.
#
# So the JVM stays native. Gradle, Kotlin compilation and dexing all run as aarch64, and
# only the three small C binaries are emulated -- which is what qemu-user is good at. The
# measured penalty is negligible: superbutton assembles in 1m 46s.
#
# QEMU_LD_PREFIX is the part that is easy to miss. binfmt will happily start an x86-64
# binary and it will then die on a missing /lib64/ld-linux-x86-64.so.2, because an aarch64
# host has no x86-64 glibc. The sysroot below was extracted from the amd64 ubuntu:24.04
# image. Note that Ubuntu ships /lib64 as a symlink to usr/lib64, which does not survive
# the extraction -- the linker has to sit at the real path the binary asks for.
#
# PREREQUISITE, needs root, run once:
#     sudo apt-get install -y qemu-user-static binfmt-support

TOOLCHAIN="$HOME/android-toolchain"

export JAVA_HOME="$TOOLCHAIN/jdk-17.0.20+8"
export ANDROID_HOME="$TOOLCHAIN/sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export QEMU_LD_PREFIX="$TOOLCHAIN/x86_64-sysroot"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$PATH"

# Fail loudly rather than half-working. A missing sysroot produces a confusing linker error
# several minutes into a build; this turns it into one line, now.
for p in "$JAVA_HOME" "$ANDROID_HOME" "$QEMU_LD_PREFIX/lib64/ld-linux-x86-64.so.2"; do
    [ -e "$p" ] || echo "android-env: MISSING $p -- see docs/LESSONS.md L-079/L-080" >&2
done
[ -e /proc/sys/fs/binfmt_misc/qemu-x86_64 ] || \
    echo "android-env: qemu-x86_64 not registered with binfmt -- run: sudo apt-get install -y qemu-user-static binfmt-support" >&2
