/*
  PAC-MAN DELUXE v3 — BlackBoard/Arduino UNO R3 + joystick + 3 LEDs
  Conexoes SEM MUDANCAS: VRx A0, VRy A1, SW D2, LEDs D8/D9/D10, 5V/GND.
  Arduino -> PC: J,x,y,botao\n (botao 1 = pressionado)
  PC -> Arduino: V:0..3\n  Sem bibliotecas externas. Envio a cada 20 ms (~50 Hz) e botao antirruido.
*/

const byte PINO_X = A0;
const byte PINO_Y = A1;
const byte PINO_BOTAO = 2;
const byte LEDS[3] = {8, 9, 10};
const unsigned long INTERVALO_MS = 20;
const unsigned long DEBOUNCE_MS = 24;

unsigned long ultimaLeitura = 0;
unsigned long ultimaTrocaBotao = 0;
byte ultimoBotaoBruto = 0;
byte botaoEstavel = 0;
int xFiltrado = 512;
int yFiltrado = 512;
char comando[12];
byte tamanhoComando = 0;

void mostrarVidas(byte vidas) {
  if (vidas > 3) vidas = 3;
  for (byte i = 0; i < 3; ++i) {
    digitalWrite(LEDS[i], i < vidas ? HIGH : LOW);
  }
}

void receberComando() {
  while (Serial.available() > 0) {
    const char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      comando[tamanhoComando] = '\0';
      if (tamanhoComando == 3 && comando[0] == 'V' && comando[1] == ':' &&
          comando[2] >= '0' && comando[2] <= '3') {
        mostrarVidas((byte)(comando[2] - '0'));
      }
      tamanhoComando = 0;
    } else if (tamanhoComando < sizeof(comando) - 1) {
      comando[tamanhoComando++] = c;
    } else {
      tamanhoComando = 0; // ignora linha serial malformada ou grande demais
    }
  }
}

void setup() {
  pinMode(PINO_BOTAO, INPUT_PULLUP);
  for (byte i = 0; i < 3; ++i) pinMode(LEDS[i], OUTPUT);
  mostrarVidas(3);
  xFiltrado = analogRead(PINO_X);
  yFiltrado = analogRead(PINO_Y);
  ultimoBotaoBruto = digitalRead(PINO_BOTAO) == LOW ? 1 : 0;
  botaoEstavel = ultimoBotaoBruto;
  ultimaTrocaBotao = millis();
  Serial.begin(115200);
}

void loop() {
  receberComando();
  const unsigned long agora = millis();
  if (agora - ultimaLeitura < INTERVALO_MS) return;
  ultimaLeitura = agora;

  // Filtro leve: suprime pequenos ruidos sem adicionar atrasos grandes.
  const int xLido = analogRead(PINO_X);
  const int yLido = analogRead(PINO_Y);
  xFiltrado = (xFiltrado * 2 + xLido * 3 + 2) / 5;
  yFiltrado = (yFiltrado * 2 + yLido * 3 + 2) / 5;

  const byte botaoBruto = digitalRead(PINO_BOTAO) == LOW ? 1 : 0;
  if (botaoBruto != ultimoBotaoBruto) ultimaTrocaBotao = agora;
  ultimoBotaoBruto = botaoBruto;
  if (agora - ultimaTrocaBotao >= DEBOUNCE_MS) botaoEstavel = botaoBruto;

  Serial.print("J,");
  Serial.print(xFiltrado);
  Serial.print(',');
  Serial.print(yFiltrado);
  Serial.print(',');
  Serial.println(botaoEstavel);
}
