// Receptor: TRANSMISION -> ENLACE -> PRESENTACION -> APLICACION

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
using socket_t = SOCKET;
using socket_len_t = int;
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
using socket_t = int;
using socket_len_t = socklen_t;
#endif

#include <exception>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "crc32_utils.h"
#include "hamming.h"

namespace {

// Determina si un descriptor de socket es inválido según la plataforma
bool socketInvalido(socket_t fd) {
#ifdef _WIN32
    return fd == INVALID_SOCKET;
#else
    return fd < 0;
#endif
}

// Lee datos desde un socket y los deja en un buffer proporcionado
int leerSocket(socket_t fd, char* buffer, int tamano) {
#ifdef _WIN32
    return recv(fd, buffer, tamano, 0);
#else
    return static_cast<int>(read(fd, buffer, tamano));
#endif
}

// Cierra un socket de forma compatible con Windows y Unix
void cerrarSocket(socket_t fd) {
#ifdef _WIN32
    closesocket(fd);
#else
    close(fd);
#endif
}

}  // namespace

// TRANSMISION
// La trama llega como dos líneas: el header y la cadena de bits
bool recibir_informacion(socket_t clienteFd, std::string& header, std::string& bitsStr) {
    std::string buffer;
    char temp[4096];
    int leidos;

    while ((leidos = leerSocket(clienteFd, temp, sizeof(temp))) > 0) {
        buffer.append(temp, leidos);
    }

    std::istringstream iss(buffer);
    return static_cast<bool>(std::getline(iss, header)) &&
           static_cast<bool>(std::getline(iss, bitsStr));
}

// Convierte una cadena de caracteres '0'/'1' en un vector de bits enteros
std::vector<int> stringABits(const std::string& s) {
    std::vector<int> bits;
    bits.reserve(s.size());
    for (char c : s) {
        if (c == '0' || c == '1') bits.push_back(c - '0');
    }
    return bits;
}

// PRESENTACION
// Convierte un bloque de bits en texto ASCII de 8 bits por carácter
bool decodificar_mensaje(const std::vector<int>& bits, std::string& texto) {
    if (bits.empty() || bits.size() % 8 != 0) return false;

    texto.clear();
    for (size_t i = 0; i < bits.size(); i += 8) {
        int valor = 0;
        for (int j = 0; j < 8; j++) {
            valor = (valor << 1) | bits[i + j];
        }
        texto.push_back(static_cast<char>(valor));
    }
    return true;
}

// APLICACION
// Los caracteres no imprimibles se muestran como \xNN para no ensuciar la terminal
std::string escapar(const std::string& texto) {
    const char* HEX = "0123456789ABCDEF";
    std::string salida;
    for (unsigned char c : texto) {
        if (c == '\\' || c == '"') {
            salida += '\\';
            salida += c;
        } else if (c >= 0x20 && c <= 0x7E) {
            salida += c;
        } else {
            salida += "\\x";
            salida += HEX[c >> 4];
            salida += HEX[c & 0x0F];
        }
    }
    return salida;
}

// Muestra el mensaje recibido cuando no hubo errores
void mostrar_mensaje(const std::string& texto) {
    std::cout << "[APLICACION]   Mensaje recibido sin errores: \"" << escapar(texto) << "\"\n";
}

// Muestra el mensaje recuperado cuando se corrigieron errores
void mostrar_mensaje_corregido(const std::string& texto, int bloques) {
    std::cout << "[APLICACION]   Errores corregidos en " << bloques
              << " bloque(s). Mensaje recuperado: \"" << escapar(texto) << "\"\n";
}

// Muestra un mensaje de error cuando la trama no pudo recuperarse
void mostrar_error() {
    std::cout << "[APLICACION]   Error detectado, no se pudo corregir. Trama descartada.\n";
}

// Procesa una trama recibida según el header: Hamming o CRC32
void procesarTrama(const std::string& header, const std::string& bitsStr) {
    std::vector<int> trama = stringABits(bitsStr);
    std::cout << "[TRANSMISION]  Header: " << header << "\n";
    std::cout << "[TRANSMISION]  Trama recibida (" << trama.size() << " bits): "
              << bitsStr << "\n";

    std::istringstream iss(header);
    std::string algoritmo, campo;
    std::getline(iss, algoritmo, ':');

    try {
        if (algoritmo == "HAMMING") {
            std::getline(iss, campo, ':');
            int m = std::stoi(campo);
            std::getline(iss, campo, ':');
            int r = std::stoi(campo);
            std::getline(iss, campo, ':');
            int longitudOriginal = std::stoi(campo);

            int corregidos = 0, irrecuperables = 0;
            bool tramaValida = false;
            std::vector<int> datos = verificarYCorregir(trama, m, r, longitudOriginal,
                                                        corregidos, irrecuperables,
                                                        tramaValida);
            std::cout << "[ENLACE]       Hamming(" << m + r << "," << m
                      << ") -> " << corregidos << " bloque(s) corregido(s), "
                      << irrecuperables << " irrecuperable(s)\n";

            std::string texto;
            if (!tramaValida || irrecuperables > 0 || !decodificar_mensaje(datos, texto)) {
                mostrar_error();
            } else if (corregidos > 0) {
                mostrar_mensaje_corregido(texto, corregidos);
            } else {
                mostrar_mensaje(texto);
            }

        } else if (algoritmo == "CRC32") {
            std::getline(iss, campo, ':');
            int longitudDatos = std::stoi(campo);

            bool integro = verificarIntegridad(trama, longitudDatos);
            std::cout << "[ENLACE]       CRC-32 -> checksum "
                      << (integro ? "correcto" : "incorrecto") << "\n";

            std::string texto;
            if (!integro) {
                mostrar_error();
            } else {
                std::vector<int> datos(trama.begin(), trama.begin() + longitudDatos);
                if (decodificar_mensaje(datos, texto)) {
                    mostrar_mensaje(texto);
                } else {
                    mostrar_error();
                }
            }

        } else {
            std::cout << "[ENLACE]       Algoritmo desconocido en el header: "
                      << algoritmo << "\n";
            mostrar_error();
        }
    } catch (const std::exception&) {
        std::cout << "[ENLACE]       Header invalido: " << header << "\n";
        mostrar_error();
    }

    std::cout << std::endl;
}

// Modo sin sockets: las tramas se pegan a mano por la entrada estándar
int modoEntradaEstandar() {
    std::string header, bitsStr;
    while (std::getline(std::cin, header)) {
        if (header.empty()) continue;
        if (!std::getline(std::cin, bitsStr)) {
            std::cerr << "Falta la linea de bits para el header: " << header << "\n";
            return 1;
        }
        procesarTrama(header, bitsStr);
    }
    return 0;
}

// Punto de entrada del programa receptor
int main(int argc, char* argv[]) {
    int puerto = 5000;
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--stdin") {
            return modoEntradaEstandar();
        }
        puerto = std::stoi(arg);
    }

#ifdef _WIN32
    WSADATA wsaData{};
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        std::cerr << "WSAStartup fallo.\n";
        return 1;
    }
#endif

    socket_t servidorFd = socket(AF_INET, SOCK_STREAM, 0);
    if (socketInvalido(servidorFd)) {
        perror("socket");
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    int opt = 1;
#ifdef _WIN32
    setsockopt(servidorFd, SOL_SOCKET, SO_REUSEADDR,
               reinterpret_cast<const char*>(&opt), sizeof(opt));
#else
    setsockopt(servidorFd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
#endif

    sockaddr_in direccion{};
    direccion.sin_family = AF_INET;
    direccion.sin_addr.s_addr = INADDR_ANY;
    direccion.sin_port = htons(puerto);

    if (bind(servidorFd, (sockaddr*)&direccion, sizeof(direccion)) != 0) {
        perror("bind");
        cerrarSocket(servidorFd);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    if (listen(servidorFd, 5) != 0) {
        perror("listen");
        cerrarSocket(servidorFd);
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    std::cout << "[TRANSMISION]  Receptor escuchando en el puerto " << puerto
              << "\n" << std::endl;

    while (true) {
        sockaddr_in clienteAddr{};
        socket_len_t clienteLen = sizeof(clienteAddr);
        socket_t clienteFd = accept(servidorFd, (sockaddr*)&clienteAddr, &clienteLen);
        if (socketInvalido(clienteFd)) {
            perror("accept");
            continue;
        }

        std::string header, bitsStr;
        if (recibir_informacion(clienteFd, header, bitsStr)) {
            procesarTrama(header, bitsStr);
        } else {
            std::cerr << "Trama incompleta, se descarta la conexion.\n";
        }

        cerrarSocket(clienteFd);
    }

    cerrarSocket(servidorFd);
#ifdef _WIN32
    WSACleanup();
#endif
    return 0;
}
