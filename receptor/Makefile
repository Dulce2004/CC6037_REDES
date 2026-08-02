# Compilador utilizado para construir el programa del receptor
CXX = g++
# Opciones de compilación: estándar C++17, advertencias activas y optimización
CXXFLAGS = -std=c++17 -Wall -O2

# En Windows se necesita la librería de sockets Winsock
ifeq ($(OS),Windows_NT)
LDLIBS = -lws2_32
else
LDLIBS =
endif

# Archivos fuente del proyecto
SRCS = receptor.cpp hamming.cpp crc32_utils.cpp
# Objetos generados a partir de los fuentes
OBJS = $(SRCS:.cpp=.o)
# Nombre del ejecutable resultado
TARGET = receptor

all: $(TARGET)

# Enlaza los objetos para producir el ejecutable final
$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) -o $(TARGET) $(OBJS) $(LDLIBS)

# Regla general para compilar cada archivo .cpp en un objeto .o
%.o: %.cpp
	$(CXX) $(CXXFLAGS) -c $< -o $@

# Limpia los archivos compilados y el ejecutable generado
clean:
	rm -f $(OBJS) $(TARGET) $(TARGET).exe

.PHONY: all clean
