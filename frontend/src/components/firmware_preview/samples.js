/* Headers de démonstration (étape 7 — Génération firmware [Flux.ai]).
 * Utilisés quand la gateway est injoignable ou qu'aucun export n'existe encore,
 * pour montrer la coloration syntaxique et le diff entre versions.
 */

export const SAMPLE_ZEPHYR_V1 = `/**
 * @file board_config.h — Drone STM32H743 + SX1276 LoRa (Zephyr RTOS)
 * Généré par pcb_ai_designer_v2 — étape 7 [Flux.ai]
 * Version 1 — génération initiale
 */
#ifndef BOARD_CONFIG_H_
#define BOARD_CONFIG_H_

#include <zephyr/kernel.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>

#define BOARD_NAME        "DRONE-H743-V2"
#define FW_VERSION_MAJOR  1
#define FW_VERSION_MINOR  0

/* Liens radio — LoRa 868 MHz via SPI1 */
#define LORA_SPI_NODE     DT_NODELABEL(spi1)
#define LORA_FREQ_HZ      868000000
#define LORA_SF           9     /* spreading factor */
#define LORA_BW_KHZ       125

/* IMU LSM6DSR — I2C1 @ 0x6A */
#define IMU_I2C_ADDR      0x6A
#define IMU_ODR_HZ        833

/* Baromètre BMP390 — I2C1 @ 0x77 */
#define BARO_I2C_ADDR     0x77

static const struct spi_config lora_spi_cfg = {
    .frequency = 8000000,
    .operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8),
};

#endif /* BOARD_CONFIG_H_ */
`;

export const SAMPLE_ZEPHYR_V2 = `/**
 * @file board_config.h — Drone STM32H743 + SX1276 LoRa (Zephyr RTOS)
 * Généré par pcb_ai_designer_v2 — étape 7 [Flux.ai]
 * Version 2 — pins LoRa + FIFO IRQ, IMU en mode haut débit
 */
#ifndef BOARD_CONFIG_H_
#define BOARD_CONFIG_H_

#include <zephyr/kernel.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/drivers/gpio.h>

#define BOARD_NAME        "DRONE-H743-V2"
#define FW_VERSION_MAJOR  2
#define FW_VERSION_MINOR  1

/* Liens radio — LoRa 868 MHz via SPI1 */
#define LORA_SPI_NODE     DT_NODELABEL(spi1)
#define LORA_FREQ_HZ      868000000
#define LORA_SF           10
#define LORA_BW_KHZ       250
#define LORA_NSS_GPIO     GPIO_DT_SPEC_GET(DT_NODELABEL(gpioa), PIN_4)
#define LORA_IRQ_GPIO     GPIO_DT_SPEC_GET(DT_NODELABEL(gpiob), PIN_0)

/* IMU LSM6DSR — I2C1 @ 0x6A */
#define IMU_I2C_ADDR      0x6A
#define IMU_ODR_HZ        1660
#define IMU_FIFO_WATERMARK 32

/* Baromètre BMP390 — I2C1 @ 0x77 */
#define BARO_I2C_ADDR     0x77

/* Boucle de contrôle */
#define CTRL_LOOP_HZ      500
#define STACK_SIZE        4096

static const struct spi_config lora_spi_cfg = {
    .frequency = 10000000,
    .operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB,
};

#endif /* BOARD_CONFIG_H_ */
`;

export const SAMPLE_ARDUINO_V1 = `/**
 * @file board_config.h — Drone STM32H743 + SX1276 LoRa (Arduino core)
 * Généré par pcb_ai_designer_v2 — étape 7 [Flux.ai]
 * Version 1 — génération initiale
 */
#ifndef BOARD_CONFIG_H_
#define BOARD_CONFIG_H_

#include <Arduino.h>
#include <SPI.h>

#define BOARD_NAME        "DRONE-H743-V2"
#define FW_VERSION        F("1.0")

// Liens radio — LoRa 868 MHz
#define PIN_LORA_NSS      PA4
#define PIN_LORA_RESET    PC13
#define PIN_LORA_IRQ      PB0
#define LORA_FREQ_MHZ     868.0
#define LORA_SF           9

// IMU / baro sur I2C1
#define PIN_SDA           PB7
#define PIN_SCL           PB6
#define IMU_I2C_ADDR      0x6A
#define BARO_I2C_ADDR     0x77

#define LED_STATUS        PB14

#endif /* BOARD_CONFIG_H_ */
`;
