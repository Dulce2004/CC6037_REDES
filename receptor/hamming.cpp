#include "hamming.h"

// Calcula la cantidad de bits de paridad necesarios para un bloque de Hamming
int calcularR(int m) {
    int r = 1;
    while ((m + r + 1) > (1 << r)) {
        r++;
    }
    return r;
}

// Determina si un número es una potencia de dos
bool esPotenciaDeDos(int x) {
    return x != 0 && (x & (x - 1)) == 0;
}

// Decodifica un bloque recibido, detecta y corrige un posible error de un bit
ResultadoBloque decodificarBloque(const std::vector<int>& recibido, int m, int r) {
    int n = m + r;
    std::vector<int> bloque(n + 1, 0);
    for (int pos = 1; pos <= n; pos++) {
        bloque[pos] = recibido[pos - 1];
    }

    // Calcula el síndrome para identificar la posición del bit erróneo
    int sindrome = 0;
    for (int k = 0; k < r; k++) {
        int p = 1 << k;
        int paridad = 0;
        for (int pos = 1; pos <= n; pos++) {
            if (pos & p) {
                paridad ^= bloque[pos];
            }
        }
        if (paridad != 0) {
            sindrome += p;
        }
    }

    bool corregido = false;
    if (sindrome != 0 && sindrome <= n) {
        bloque[sindrome] ^= 1;
        corregido = true;
    }

    std::vector<int> datos;
    for (int pos = 1; pos <= n; pos++) {
        if (!esPotenciaDeDos(pos)) {
            datos.push_back(bloque[pos]);
        }
    }

    return {datos, corregido, sindrome};
}

// Recorre toda la trama, corrige bloques individuales y reconstruye los datos
std::vector<int> verificarYCorregir(const std::vector<int>& trama, int m, int r,
                                    int longitudOriginal,
                                    int& bloquesCorregidos,
                                    int& bloquesIrrecuperables,
                                    bool& tramaValida) {
    int n = m + r;
    bloquesCorregidos = 0;
    bloquesIrrecuperables = 0;
    tramaValida = (n > 0) && !trama.empty() && (trama.size() % n == 0);

    std::vector<int> datos;
    if (!tramaValida) {
        return datos;
    }

    for (size_t i = 0; i + n <= trama.size(); i += n) {
        std::vector<int> bloque(trama.begin() + i, trama.begin() + i + n);
        ResultadoBloque resultado = decodificarBloque(bloque, m, r);

        if (resultado.sindrome != 0) {
            if (resultado.corregido) {
                bloquesCorregidos++;
            } else {
                bloquesIrrecuperables++;
            }
        }

        datos.insert(datos.end(), resultado.datos.begin(), resultado.datos.end());
    }

    if ((int)datos.size() < longitudOriginal) {
        tramaValida = false;
        return datos;
    }

    datos.resize(longitudOriginal);
    return datos;
}
