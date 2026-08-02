#include "crc32_utils.h"

// Convierte una secuencia de bits en bytes, completando con ceros si es necesario
std::vector<uint8_t> bitsABytes(const std::vector<int>& bitsIn) {
    std::vector<int> bits = bitsIn;
    int resto = bits.size() % 8;
    if (resto != 0) {
        for (int i = 0; i < 8 - resto; i++) {
            bits.push_back(0);
        }
    }

    std::vector<uint8_t> bytes;
    for (size_t i = 0; i < bits.size(); i += 8) {
        uint8_t byte = 0;
        for (int j = 0; j < 8; j++) {
            byte = (byte << 1) | bits[i + j];
        }
        bytes.push_back(byte);
    }
    return bytes;
}

// Calcula el CRC-32 de un conjunto de datos usando una tabla precomputada
uint32_t crc32(const std::vector<uint8_t>& datos) {
    static uint32_t tabla[256];
    static bool inicializada = false;
    const uint32_t POLY = 0xEDB88320u;

    if (!inicializada) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) {
                c = (c & 1) ? (POLY ^ (c >> 1)) : (c >> 1);
            }
            tabla[i] = c;
        }
        inicializada = true;
    }

    uint32_t crc = 0xFFFFFFFFu;
    for (uint8_t byte : datos) {
        crc = tabla[(crc ^ byte) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}

// Verifica si la trama recibida tiene el CRC correcto para los datos originales
bool verificarIntegridad(const std::vector<int>& trama, int longitudDatos) {
    if (longitudDatos < 0 || (int)trama.size() != longitudDatos + 32) {
        return false;
    }

    std::vector<int> datos(trama.begin(), trama.begin() + longitudDatos);

    uint32_t recibido = 0;
    for (size_t i = longitudDatos; i < trama.size(); i++) {
        recibido = (recibido << 1) | trama[i];
    }

    return crc32(bitsABytes(datos)) == recibido;
}
