/*
  Pac-Man - Arduino UNO/Nano + joystick 5 pinos + 3 LEDs.
  Envia USB: J,valorX,valorY,botao\n  Recebe USB: V:3\n (3, 2, 1 ou 0 vidas).
  Monitor Serial e Python não podem usar a mesma COM ao mesmo tempo.
*/

const byte PINO_X = A0;
const byte PINO_Y = A1;
const byte PINO_BOTAO = 2;
const byte LEDS[3] = {8, 9, 10};
const unsigned long INTERVALO_MS = 40;
unsigned long ultimaLeitura = 0;
char comando[12];
byte tamanhoComando = 0;

void mostrarVidas(byte vidas) {
  for (byte i = 0; i < 3; i++) {
    digitalWrite(LEDS[i], i < vidas ? HIGH : LOW);
  }
}

void receberComando() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      comando[tamanhoComando] = '\0';
      if (tamanhoComando == 3 && comando[0] == 'V' &&
          comando[1] == ':' && comando[2] >= '0' && comando[2] <= '3') {
        mostrarVidas((byte)(comando[2] - '0'));
      }
      tamanhoComando = 0;
    } else if (tamanhoComando < sizeof(comando) - 1) {
      comando[tamanhoComando++] = c;
    } else {
      tamanhoComando = 0;
    }
  }
}

void setup() {
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  for (byte i = 0; i < 3; i++) {
    pinMode(LEDS[i], OUTPUT);
  }
  mostrarVidas(3);
  Serial.begin(115200);
}

void loop() {
  receberComando();
  unsigned long agora = millis();
  if (agora - ultimaLeitura >= INTERVALO_MS) {
    ultimaLeitura = agora;
    int x = analogRead(PINO_X);
    int y = analogRead(PINO_Y);
    byte apertado = digitalRead(PINO_BOTAO) == LOW ? 1 : 0;
    Serial.print("J,");
    Serial.print(x);
    Serial.print(',');
    Serial.print(y);
    Serial.print(',');
    Serial.println(apertado);
  }
}
