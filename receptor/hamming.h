#ifndef HAMMING_H
#define HAMMING_H

#include <vector>

// Representa el resultado de decodificar un bloque de Hamming
struct ResultadoBloque {
    std::vector<int> datos;  // Bits de datos extraídos del bloque
    bool corregido;          // Indica si se corrigió un bit en este bloque
    int sindrome;            // Valor del síndrome usado para detectar la posición del error
};

// Calcula la cantidad mínima de bits de paridad r para un bloque de Hamming
int calcularR(int m);

// Determina si un entero es una potencia de dos
bool esPotenciaDeDos(int x);

// Decodifica un bloque recibido, detecta y corrige un error de un bit si existe
ResultadoBloque decodificarBloque(const std::vector<int>& recibido, int m, int r);

// Verifica y corrige la trama completa y devuelve los bits de datos sin padding
// La bandera tramaValida se pone en false si la trama no tiene un número entero de bloques
std::vector<int> verificarYCorregir(const std::vector<int>& trama, int m, int r,
                                    int longitudOriginal,
                                    int& bloquesCorregidos,
                                    int& bloquesIrrecuperables,
                                    bool& tramaValida);

#endif
