# Calibração de Voz: feminina-v1

## Status

`feminina-v1` é um perfil oficial alternativo do renderizador local
`chatterbox-multilingual-v3-pt-br`; o padrão de produção é `masculina-v1`. O perfil
feminino foi promovido por uma seleção com três textos, não apenas pela proximidade
no prompt principal.

Este documento registra a calibração concluída em 14 de julho de 2026. Ele não
afirma que o Chatterbox reproduz exatamente a fonte em qualquer texto novo. A
decisão é válida para a referência, corpus, runtime e modelo hashados abaixo.
O registro estruturado de promoção é
`plugins/audiobook-codex/assets/voices/feminina-v1.promotion.json`.

## Origem e Entradas Imutáveis

- A fonte-alvo foi gerada externamente com ElevenLabs, voz Amanda Kelly PT-BR e
  modelo V3, conforme informado pelo usuário. Os áudios foram importados
  localmente; nenhuma chave ou chamada de API faz parte da produção.
- Referência acondicionadora: `plugins/audiobook-codex/assets/voices/Feminina.mp3`.
- SHA-256 da referência: `20d890c2a97bc2dd97b4ea4021e83681c00830c7b2e8894f944776e44eacde9f`.
- Formato da referência: MP3 mono, 44,1 kHz, 128 kbps, duração de 24,921 s.
- Corpus: `E:\Pessoal\e-books\_voice-calibration-feminina\validation-corpus\corpus.json`.
- SHA-256 do corpus: `908c271cc1e268510910ee5bba119dc69dbaa8686538a97a87c61739ac5d09a6`.
- Seleção final:
  `E:\Pessoal\e-books\_voice-calibration-feminina\cross-prompt-selection-minp-final-2026-07-14\selection.json`.
- SHA-256 da seleção: `656a9e32a603967c9dc2dd3dffd61f67248ba53aeb955b7f00f79ef6aba6a753`.

## Corpus de Validação

Os marcadores de direção entre colchetes do experimento inicial não entraram no
texto efetivamente renderizado. Eles não são controles suportados pelo fluxo de
produção e tornam a comparação entre motores ambígua.

1. `01-narracao`

   > Na manhã de junho, a chuva fina cobria o jardim, enquanto a brisa movia lentamente as folhas. O relógio marcou oito e trinta. João abriu a janela e perguntou: "Quem deixou a pequena caixa azul junto à porta?" Após um breve silêncio, respirou devagar e disse: "Muito bem. Hoje começa uma nova história."

2. `02-dialogo`

   > Quando Clara entrou na sala, encontrou as janelas abertas e os papéis espalhados sobre a mesa. Ela respirou fundo e perguntou, "Alguém esteve aqui?" Ninguém respondeu. Então fechou a porta, guardou a carta no bolso e disse, "Vamos descobrir isso antes do amanhecer."

3. `03-semiotica`

   > Na sexta-feira, três de abril de dois mil e vinte e seis, às quatorze horas e trinta minutos, o museu recebeu vinte e cinco visitantes. O ingresso custava quarenta e dois reais e cinquenta centavos. Ana anotou tudo no caderno e avisou que a próxima visita seria às nove horas.

O corpus cobre narração, diálogo e fala com data, hora, quantidade e valor. Cada
prompt e seu áudio-alvo estão hashados no manifesto.

## Método

Cada candidato foi renderizado localmente, comparado ao respectivo alvo e retido
em um manifesto com parâmetros, seed, duração e hashes. A métrica por prompt foi:

```text
composite =
  0.48 * similaridade do embedding de voz +
  0.20 * similaridade de duração +
  0.32 * alinhamento acústico
```

O alinhamento acústico ponderou perfil de energia (0,28), fala (0,18), pitch
(0,18), fração sonora (0,08), MFCC (0,18) e centróide espectral (0,10).

Para escolher um único perfil foi usado:

```text
robustez = 0.7 * média dos três composites + 0.3 * menor composite
```

Empates usam maior média, depois maior mínimo e, por fim, menor desvio padrão.
Essas métricas servem para ordenar candidatos dentro deste corpus. Não devem ser
comparadas como uma escala universal entre outra voz, outro corpus ou outro motor.

## Rodadas Executadas

O workspace preserva todas as saídas em
`E:\Pessoal\e-books\_voice-calibration-feminina`. A investigação ocorreu em
camadas para evitar otimizar apenas uma variável:

1. Baseline e rodadas `round1` a `round8-cfg`: primeira aproximação, seeds,
   amostragem, ajuste fino, expressividade, penalidade de repetição e CFG.
2. Rodadas de produção: `production-seeds`, `production-exag-fine`,
   `production-temperature-fine`, `production-topp-fine`,
   `production-penalty-fine`, `production-minp-fine`, `production-cfg-fine` e
   `production-cfg-ultrafine`.
3. Referências alternativas V3: as variantes `04-viva` e
   `05_expressiva_suspiro` foram renderizadas no mesmo texto. As melhores
   pontuações do prompt principal foram 0,851379 e 0,802335; nenhuma superou a
   referência original.
4. Seleção cross-prompt: cinco finalistas foram renderizados para os três
   prompts com o estado aleatório controlado.
5. Referência Eleven V2: a referência V2 não substituiu a V3.
6. Pós-processamento: normalização, entrega MP3, shelf de EQ e limitação foram
   comparados contra o WAV bruto.
7. Reprodução de implementação: o renderizador de produção foi ajustado para
   carregar o modelo local em CUDA e a saída aprovada foi reproduzida.

## Perfil Vencedor

```text
nome: feminina-v1
motor: chatterbox-multilingual-v3-pt-br
runtime: chatterbox-tts 0.1.7
modelo: ResembleAI/Chatterbox-Multilingual-pt-br local
dispositivo de promoção: cuda
max_chars: 320
silence_seconds: 0.22
exaggeration: 0.55
cfg_weight: 0.502
temperature: 0.8
repetition_penalty: 1.2
min_p: 0.114
top_p: 1.0
seed: 20260713
```

O candidato vencedor foi `minp-0-114-temp-0-80`.

| Prompt | Composite | Duração |
| --- | ---: | ---: |
| `01-narracao` | 0,713290 | 24,840 s |
| `02-dialogo` | 0,602714 | 20,120 s |
| `03-semiotica` | 0,578662 | 19,480 s |

Resultado agregado:

```text
robustez: 0.615687
média: 0.631555
mínimo: 0.578662
desvio padrão: 0.058623
```

O perfil também preservou o WAV aprovado do prompt principal, byte a byte:

```text
SHA-256: 5c9e0f38e679c03b99ca0c01318f0a668d47f14e453510a89dcad927d416471b
duração: 24.840 s
```

O MP3 de entrega correspondente tem SHA-256
`9299bbe5c869bae3dac911b066cb64d37964e0f7074ff1ca06614a08b55b4a4e`.

No experimento específico do prompt principal, o WAV bruto obteve proxy de
proximidade `0.901946` e cosseno de embedding `0.967617`. Esse proxy não é a
mesma medida agregada da seleção cross-prompt e não deve ser comparado
diretamente com `0.615687`.

## Referência V2 e Pós-Processamento

A referência Eleven V2 teve duração de 19,304 s contra 24,921 s da V3. Usando o
mesmo texto, seed e parâmetros:

- a condição V2 marcou `0.647792` contra o alvo V3;
- contra o próprio alvo V2, ela marcou `0.631944`;
- a saída condicionada pela V3 marcou `0.635452` contra esse mesmo alvo V2.

Ela não trouxe ganho consistente e foi descartada.

O WAV bruto foi mantido como sinal de produção:

| Variante | Proxy de proximidade |
| --- | ---: |
| WAV bruto | 0,901946 |
| Loudness normalizado | 0,900343 |
| MP3 mono 44,1 kHz / 128 kbps | 0,894049 |
| Melhor shelf de EQ | 0,877240 |

Não foram adotados EQ, compressor, de-esser, limiter ou loudnorm como padrão. O
MP3 continua sendo apenas o formato de entrega.

## Integração de Produção

`render_chatterbox.py` reconhece `feminina-v1` somente quando a referência, seus
hashes, os hashes dos checkpoints, a versão `chatterbox-tts`, o dispositivo CUDA e
todos os parâmetros correspondem ao registro de promoção. O renderizador aplica a
política de texto do perfil, mas não restringe cada render a um único hash de entrada.
Caso contrário, o manifesto declara o perfil como `custom`.

O texto do locutor deve ter uma locução completa por linha não vazia, até 320
caracteres. Números e abreviações devem ser escritos por extenso em PT-BR. O
renderizador rejeita dígitos, marcação entre colchetes, SSML/HTML, Markdown, URLs,
emails e abreviações comuns.

## Reprodução

```powershell
$python = 'E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe'

& $python .\plugins\audiobook-codex\scripts\render_chatterbox.py `
  --input-file E:\Pessoal\e-books\_voice-calibration-feminina\prompt-rendered.txt `
  --output-dir E:\Pessoal\e-books\_voice-calibration-feminina\repro-run `
  --standalone --format mp3 --device cuda --seed 20260713
```

O resultado deve ser verificado contra o hash do WAV acima, não apenas pelo nome
do perfil. Mesmo usando `--format mp3`, o renderizador preserva o sinal bruto em
`E:\Pessoal\e-books\_voice-calibration-feminina\repro-run\raw\audiobook.wav`;
confira a chave `final_wav_sha256` em
`E:\Pessoal\e-books\_voice-calibration-feminina\repro-run\audio-manifest.json`.

## Limitações e Próxima Calibração

- A revisão auditiva continua obrigatória para candidatos finalistas.
- Uma nova voz, novo alvo, novo modelo local, versão de runtime, checkpoint ou
  política de segmentação inicia uma nova calibração.
- A próxima execução deve usar `$voice-calibration`; ela cria um novo workspace,
  congela o mesmo corpus e só promove um perfil após evidência equivalente.
