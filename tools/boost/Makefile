SDK ?=
OUT ?= /tmp/tunu-boost-native
CROSS ?= arm-none-eabi-
CC := $(CROSS)gcc
OBJCOPY := $(CROSS)objcopy
CFLAGS := -std=gnu11 -Os -Wall -Wextra -Werror -fpic -mthumb -mcpu=cortex-m4
CFLAGS += -mfloat-abi=hard -mfpu=fpv4-sp-d16 -DIS_VESC_LIB -I$(SDK)
CFLAGS += -ffunction-sections -fdata-sections -fomit-frame-pointer -falign-functions=16
LDFLAGS := -nostartfiles -static -mthumb -mcpu=cortex-m4 -mfloat-abi=hard -mfpu=fpv4-sp-d16
LDFLAGS += -Wl,--gc-sections,--undefined=init -T$(SDK)/link.ld

.PHONY: all check-sdk
all: $(OUT)/boost_guard.json

check-sdk:
	@test -f "$(SDK)/vesc_c_if.h" -a -f "$(SDK)/link.ld" || \
		{ echo 'Set SDK to the matching VESC lispBM/c_libs directory' >&2; exit 1; }

$(OUT):
	mkdir -p "$@"

$(OUT)/boost_guard.o: boost_guard.c boost_core.h check-sdk | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/boost_core.o: boost_core.c boost_core.h check-sdk | $(OUT)
	$(CC) $(CFLAGS) -c $< -o $@

$(OUT)/boost_guard.elf: $(OUT)/boost_guard.o $(OUT)/boost_core.o
	$(CC) $(LDFLAGS) $^ -lm -o $@

$(OUT)/boost_guard.bin: $(OUT)/boost_guard.elf
	$(OBJCOPY) -O binary --gap-fill 0 $< $@

$(OUT)/boost_guard.json: $(OUT)/boost_guard.bin manifest.py
	python3 manifest.py --sdk "$(SDK)" --binary "$<"
