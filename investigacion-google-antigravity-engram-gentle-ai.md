# Investigación técnica: Google AI Studio / Antigravity Agent + Engram + Gentle-AI

**Fecha de preparación:** 4 de noviembre de 2026  
**Tipo de trabajo:** investigación de viabilidad y diseño preliminar; todavía no implementar  
**Destinatario:** agente de programación que trabaja localmente en el ordenador del usuario  
**Idioma del informe:** español

---

## 0. Encargo y resultado que buscamos

Quiero investigar cómo construir un entorno/agente de programación que aproveche al máximo la infraestructura que Google ya proporciona y, al mismo tiempo, incorpore las capacidades de estos dos proyectos:

- Engram: <https://github.com/Gentleman-Programming/engram>
- Gentle-AI: <https://github.com/Gentleman-Programming/gentle-ai>

También quiero valorar si conviene reutilizar las herramientas oficiales de Google tal como están o desarrollar nuestra propia CLI que las coordine y añada las capacidades de Engram y Gentle-AI.

La investigación debe responder dos preguntas principales:

1. **Integración nativa/adaptada:** ¿podemos utilizar Engram y Gentle-AI con el andamiaje que Google ya ofrece, especialmente Antigravity Agent / Managed Agents, sus entornos remotos, skills, `AGENTS.md`, MCP, Interactions API y Gemini API CLI?
2. **CLI propia:** ¿podemos construir una CLI local que use la infraestructura de Google como motor de ejecución, incorpore Engram como memoria persistente y aplique las metodologías, skills y mecanismos de ingeniería de Gentle-AI?

No presupongas que la respuesta correcta sea desarrollar una CLI desde cero. Compara el uso directo de las herramientas de Google, una capa fina de orquestación propia y una implementación más completa. **No reimplementes el harness de Antigravity si Google ya nos proporciona uno suficientemente bueno.**

El objetivo no es crear simplemente un wrapper que envíe prompts a Gemini. Queremos evaluar un sistema de trabajo de ingeniería con:

- continuidad entre sesiones;
- memoria persistente y contextual por proyecto;
- metodología de desarrollo rigurosa y proporcional;
- skills especializadas y recuperables;
- ejecución real de comandos, pruebas y builds;
- gestión fiable de sesiones, entornos, archivos y resultados;
- acceso seguro al proyecto local o a su copia remota;
- posibilidad de utilizar tanto la interfaz de Google como una CLI propia;
- costes, privacidad, permisos y trazabilidad controlables.

### Restricción inicial: solo investigar

Esta primera fase es de **diagnóstico y diseño, no de implementación**.

- No instales, actualices ni desinstales herramientas.
- No ejecutes instaladores remotos con `curl | bash` o equivalentes.
- No modifiques configuraciones globales del usuario ni repositorios existentes.
- No crees recursos facturables en Google, ejecutes agentes remotos ni consumas cuota sin autorización explícita.
- No abras puertos, túneles, servidores públicos ni expongas servicios locales.
- No leas ni imprimas claves API, tokens, contraseñas, credenciales o contenidos de `.env`.
- Puedes inspeccionar de forma local y de solo lectura las herramientas instaladas, sus versiones, los repositorios, sus configuraciones no secretas y el entorno de desarrollo.
- Puedes consultar documentación y repositorios públicos actuales.
- Si alguna comprobación requiere una acción que cree recursos, modifique datos, transmita código privado o genere costes, descríbela como experimento futuro y espera autorización.

Puedes entregar el análisis en la conversación. No alteres los proyectos existentes. Si se solicita un archivo de informe, crea únicamente un archivo nuevo de investigación y no sobrescribas nada.

---

## 1. Contexto: los descubrimientos en el orden en que aparecieron

### 1.1. Google AI Studio Build — `https://aistudio.google.com/apps`

Es el entorno visual de AI Studio para crear aplicaciones web full-stack y aplicaciones Android nativas mediante prompts, código y previsualización.

Aspectos relevantes:

- Puede importar un repositorio desde GitHub.
- Puede vincular una aplicación a GitHub y mantener sincronización bidireccional: cambios hechos en AI Studio pueden enviarse al repositorio y cambios realizados localmente pueden recuperarse desde AI Studio.
- La documentación de Google indica que las Gemini Agent Skills están preinstaladas en AI Studio Build.
- Build es una experiencia de creación de aplicaciones. No hay que confundirla con la interfaz Playground para Managed Agents ni asumir que expone exactamente las mismas herramientas y opciones de configuración.

Documentación:

- <https://aistudio.google.com/docs/aistudio-build-mode>
- <https://aistudio.google.com/docs/coding-agents>

**Qué debe verificarse:** qué personalización del agente de Build está realmente soportada hoy, si permite aportar `AGENTS.md` y skills propias, si admite MCP externo de forma directa y qué capacidades se comparten —o no— con Antigravity Agent. No deducir compatibilidad de una función de Managed Agents a partir de que Build tenga skills preinstaladas.

### 1.2. Engram — memoria persistente

Repositorio: <https://github.com/Gentleman-Programming/engram>

Engram es una capa de memoria persistente para agentes de programación. No es un agente ni un modelo. Su núcleo es una aplicación Go con SQLite + FTS5 y varias interfaces:

- CLI (`engram`);
- TUI (`engram tui`);
- MCP server;
- API HTTP local;
- Git Sync para exportar/importar recuerdos de forma portable;
- sincronización opcional con Engram Cloud.

Su enfoque es local-first: SQLite local es la fuente de verdad; Git Sync y Cloud son mecanismos opcionales para compartir o replicar datos.

El protocolo espera que el agente:

1. confirme el proyecto actual;
2. recupere el contexto pertinente al inicio de un trabajo;
3. busque memoria antes de repetir decisiones o investigaciones;
4. recupere progresivamente los registros completos cuando sean necesarios;
5. guarde decisiones, descubrimientos, correcciones y restricciones duraderas, no cada turno de conversación;
6. actualice temas con claves estables (`topic_key`);
7. deje un resumen/handoff antes de terminar;
8. recupere el resumen después de una compactación de contexto.

Herramientas MCP importantes que deben estudiarse: `mem_current_project`, `mem_context`, `mem_search`, `mem_get_observation`, `mem_save`, `mem_session_summary` y las herramientas de sesión pertinentes. No asumir que la lista o los contratos siguen idénticos: comprobar el esquema actual en el código y en `DOCS.md`.

Integración local documentada con varios agentes, incluido Antigravity CLI mediante `engram setup antigravity-cli`. Esa integración es para el cliente Antigravity local soportado por Engram; **no demuestra por sí sola que funcione con el Antigravity Agent administrado en la nube**.

Fuentes:

- <https://github.com/Gentleman-Programming/engram>
- <https://github.com/Gentleman-Programming/engram/blob/main/DOCS.md>
- <https://github.com/Gentleman-Programming/engram/blob/main/docs/AGENT-SETUP.md>
- <https://github.com/Gentleman-Programming/engram/blob/main/docs/ARCHITECTURE.md>
- <https://github.com/Gentleman-Programming/engram/blob/main/docs/HTTP-AGENT-WORKFLOWS.md>
- <https://github.com/Gentleman-Programming/engram/blob/main/docs/engram-cloud/README.md>

### 1.3. Gentle-AI — metodología y entorno de ingeniería

Repositorio: <https://github.com/Gentleman-Programming/gentle-ai>

Gentle-AI no instala el modelo ni sustituye al agente. Configura agentes existentes y les proporciona una metodología de trabajo, instrucciones, skills, herramientas, personas y mecanismos de revisión.

Componentes y conceptos relevantes:

- **ODD (Organic Driven Development):** flujo proporcional al entendimiento, riesgo y necesidad de reanudación. Primero determina la autorización, explora, resuelve incertidumbres reales, implementa cuando está autorizado y verifica de forma proporcionada. El trabajo pequeño no debe generar burocracia; el trabajo sustancial conserva un documento de feature recuperable.
- **Engram:** memoria persistente integrada como componente.
- **Skills:** biblioteca de procedimientos especializados.
- **Context7:** MCP para documentación actualizada de frameworks y librerías.
- **CodeGraph:** grafo de símbolos/relaciones del código, de solo lectura.
- **RDD (Receipt-Driven Development):** mecanismo de revisión del cambio exacto, con autoridad y evidencia asociadas a una versión congelada del candidato. No confundirlo con pedir al modelo que “revise bien”.
- **Personas e instrucciones:** pueden orientar comportamiento, tono y responsabilidades.
- **Guardrails/permisos:** configuraciones específicas de algunos clientes, con límites que dependen del host.
- **Agentes/revisores y delegación:** dependen de las capacidades nativas de cada agente.

Gentle-AI dispone de una CLI instaladora/administradora (`gentle-ai install`, `sync`, `doctor`, etc.) que escribe configuraciones específicas de cada cliente. Su integración Antigravity está orientada al cliente Antigravity local y a sus rutas/configuración nativas. No hay que suponer que `gentle-ai install --agent antigravity` configure automáticamente un Antigravity Agent remoto invocado por API.

La documentación de `main` puede incluir cambios aún no publicados. Comparar siempre `main` con la última release estable y distinguir claramente funciones publicadas, beta y cambios no liberados. La documentación consultada en esta investigación hace referencia a la release v4.0.0 como documentación de la última release; verificar el estado real antes de tomar decisiones.

Fuentes:

- <https://github.com/Gentleman-Programming/gentle-ai>
- <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/intended-usage.md>
- <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/usage.md>
- <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/agents.md>
- <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/components.md>
- <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/engram.md>

### 1.4. AI Studio Playground — Agents / Managed Agents

Documentación: <https://aistudio.google.com/docs/aistudio-agents>

Google AI Studio tiene una experiencia visual para prototipar agentes administrados. La documentación indica acceder a Playground y cambiar el selector a Agents. El usuario ha confirmado que ya encontró Managed Agents en su cuenta.

La documentación oficial describe:

- plantillas de agentes basadas en Antigravity Agent;
- panel de entorno y fuentes;
- herramientas integradas como Google Search, URL Context, Code Execution y Filesystem;
- instrucciones `AGENTS.md` cargadas en el contexto del agente;
- skills en `.agents/skills/<skill-name>/SKILL.md`;
- fuentes montables: archivos inline, GitHub y Google Cloud Storage;
- posibilidad de reutilizar un Environment ID existente;
- descarga del snapshot del entorno.

Las fuentes se fijan al iniciar la primera interacción: durante una ejecución no se pueden cambiar libremente los mounts ni la allowlist de red. Comprobar los detalles actuales en la documentación.

Fuentes:

- <https://aistudio.google.com/docs/aistudio-agents>
- <https://aistudio.google.com/docs/agent-environment>
- <https://github.com/google-gemini/gemini-managed-agents-templates>

### 1.5. Antigravity Agent — agente administrado de Gemini API

Documentación: <https://aistudio.google.com/docs/antigravity-agent>

Este fue el descubrimiento central. Google describe Antigravity Agent como un agente administrado de propósito general disponible mediante Gemini API y AI Studio. Una llamada a Interactions API puede provisionar un sandbox Linux alojado por Google, donde el agente planifica, ejecuta herramientas, observa resultados y continúa hasta completar la tarea.

El identificador que aparece en la documentación consultada es `antigravity-preview-09-2026`. La documentación indica Gemini 3.8 Flash como modelo predeterminado y permite configurar otro modelo mediante `agent_config`; comprobar los identificadores y la disponibilidad actual antes de depender de ellos.

Capacidades documentadas:

- ejecución de Bash, Python y Node.js;
- instalación de dependencias, pruebas y builds;
- lectura, escritura, edición, búsqueda y listado de archivos en el sandbox;
- acceso web mediante Google Search y URL Context;
- compactación automática del contexto (la documentación menciona aproximadamente 135k tokens);
- funciones personalizadas (`function`);
- servidores MCP remotos (`mcp_server`) mediante **Streamable HTTP**;
- entornos remotos nuevos, existentes o definidos mediante configuración;
- continuidad de archivos/paquetes al reutilizar el mismo `environment_id`.

Es importante separar:

- Antigravity IDE / Antigravity CLI (`agy`): cliente/entorno local de desarrollo.
- Antigravity Agent: agente administrado que se ejecuta en un sandbox de Google y se invoca por API/UI.
- Gemini API CLI (`gemini-api`): otro programa, que llama a Interactions API desde terminal.
- Gemini CLI (`gemini`): herramienta distinta; no mezclar nombres ni configuraciones.

Google afirma que Antigravity Agent utiliza el mismo harness que Antigravity IDE. No interpretar esa afirmación como paridad automática de todas las configuraciones, plugins, hooks, acceso al filesystem local o funciones del IDE. Verificar cada capacidad.

Fuentes:

- <https://aistudio.google.com/docs/antigravity-agent>
- <https://aistudio.google.com/docs/managed-agents-quickstart>
- <https://aistudio.google.com/docs/agent-environment>
- <https://aistudio.google.com/docs/interactions-overview>

### 1.6. Coding Agents — recursos oficiales de Google y Gemini API CLI

Documentación: <https://aistudio.google.com/docs/coding-agents>

Esta página agrupa recursos para que los agentes de programación utilicen prácticas y documentación actualizadas de Gemini:

1. **Gemini Agent Skills:** reglas y patrones procedimentales; el repositorio incluye `gemini-api-dev`, `gemini-live-api-dev` y `gemini-omni-flash-api`. La documentación indica que las skills se incluyen en AI Studio Build y pueden montarse en Managed Agents.
2. **Gemini Docs MCP:** servidor público `https://gemini-api-docs-mcp.dev`, con las herramientas `gemini_search_docs` y `gemini_get_doc`.
3. **Gemini API Plugin:** paquete para Antigravity que agrupa skills, Gemini Docs MCP y herramientas relacionadas con API keys.
4. **Machine-readable docs:** Markdown, `llms.txt` y contenido pensado para agentes.
5. **Gemini API CLI (`gemini-api`):** CLI de Google para generar contenido, analizar archivos y ejecutar/administrar agentes administrados desde shell sin escribir directamente llamadas SDK.

Fuentes:

- <https://aistudio.google.com/docs/coding-agents>
- <https://github.com/google-gemini/gemini-skills>
- <https://github.com/google-gemini/gemini-api-cli>
- <https://gemini-api-docs-mcp.dev>

**Cuidado con la nomenclatura:** `gemini-api` no es `gemini` (Gemini CLI) ni `agy` (Antigravity CLI).

---

## 2. Andamiaje de Google que debemos evaluar como base

No queremos reconstruir lo que Google ya hace bien. Inspecciona y documenta las capacidades disponibles hoy, sus límites, su estado de disponibilidad y las APIs exactas.

### 2.1. Interactions API

Documentación: <https://aistudio.google.com/docs/interactions-overview>

La documentación consultada la presenta como interfaz recomendada para nuevos proyectos. Características importantes:

- una misma API para modelos Gemini y agentes administrados;
- historial server-side mediante `previous_interaction_id`;
- observabilidad de pasos;
- ejecución en background mediante `background=true`;
- opción `store=false` para no conservar interacciones en el almacenamiento del servicio;
- combinación de distintas interacciones de agentes/modelos en una secuencia.

**No confundir continuidad conversacional de Google con memoria duradera de proyecto.** `previous_interaction_id` conserva inputs/outputs de una conversación; Engram mantiene recuerdos seleccionados y organizados por proyecto. Son funciones complementarias.

La documentación consultada indica retención por defecto de interacciones almacenadas de 1 día en Free y 55 días en Paid, configurable para proyectos Paid con plazos de 7, 14, 28 o 55 días. `store=false` no se puede combinar con background ni con continuidad por `previous_interaction_id`. Verificar valores, plan y términos actuales antes de diseñar el almacenamiento.

### 2.2. Entornos remotos

Documentación: <https://aistudio.google.com/docs/agent-environment>

La API acepta, entre otras, estas formas de entorno:

- `"remote"`: nuevo sandbox;
- Environment ID: reutilizar sandbox existente con archivos y paquetes;
- objeto de configuración: fuentes, red, variables de entorno y otras opciones.

Fuentes montables documentadas:

- repositorios públicos o privados de GitHub;
- Google Cloud Storage;
- archivos inline.

El sandbox proporciona Ubuntu y paquetes comunes, Python 3.12 y Node.js 22, según la documentación consultada. Los paquetes instalados permanecen si se reutiliza el mismo `environment_id`. Verificar expiración, límites de almacenamiento, CPU/RAM, persistencia real, snapshot, costes y reglas de limpieza.

### 2.3. Herramientas y MCP

Documentación: <https://aistudio.google.com/docs/antigravity-agent#supported-tools>

Herramientas documentadas:

- `code_execution`;
- `google_search`;
- `url_context`;
- filesystem habilitado al especificar un entorno;
- custom functions (`function`);
- remote MCP (`mcp_server`).

El servidor MCP remoto debe ser accesible por HTTPS/URL y hablar Streamable HTTP. La configuración documentada contiene `type: "mcp_server"`, `name` (identificador en minúsculas), `url`, headers opcionales y `allowed_tools` opcional.

### 2.4. Dos formas de integrar servicios locales: investigar ambas

**A. Custom function calling (muy importante para Engram).**

La documentación de Antigravity Agent muestra una secuencia de dos turnos:

1. el agente solicita una función;
2. el cliente que inició la interacción ejecuta esa función y devuelve su resultado en la siguiente interacción usando la continuidad correspondiente.

La documentación actual señala que custom function calling es solo stateful y requiere `previous_interaction_id`. Investiga si nuestra CLI local podría actuar como ejecutor de funciones de Engram. Esta opción puede ser especialmente atractiva porque el cliente de nuestra propiedad corre en el Mac y podría ejecutar operaciones contra Engram local sin exponer un servidor de memoria a Internet.

Documentación: <https://aistudio.google.com/docs/antigravity-agent#function-calling>

**B. Servidor MCP remoto Streamable HTTP.**

El agente puede conectarse a un MCP remoto con URL HTTPS, headers y lista de herramientas permitidas. Es una opción limpia si el servicio ya está desplegado de forma segura, pero Engram documenta su MCP normal por `stdio`, no asumir que su MCP actual ya ofrece Streamable HTTP.

Documentación: <https://aistudio.google.com/docs/antigravity-agent#mcp-servers>

No crear ni exponer un endpoint durante esta fase. Primero estudiar el contrato y el modelo de seguridad.

### 2.5. Variables, credenciales y red

La documentación de entornos distingue variables literales y referencias a credenciales guardadas. Para secretos, estudiar la opción de credenciales administradas; no insertar claves en archivos, prompts, repositorios ni texto que el agente pueda leer.

La conectividad de red puede configurarse mediante allowlist y transformaciones de headers. No usar `domain: "*"` salvo que se justifique expresamente. Evaluar los dominios mínimos necesarios para GitHub, Engram gateway, Google APIs y documentación.

Documentación:

- <https://aistudio.google.com/docs/agent-environment#environment-variables>
- <https://aistudio.google.com/docs/agent-credentials>

### 2.6. Coste y control de ejecución

El agente puede ejecutar ciclos autónomos de planificación/acción/observación; una instrucción abierta puede generar consumo elevado. Toda prueba debe tener objetivo, límite y criterio de terminación claros. La API permite background y cancelación, pero la ejecución y almacenamiento pueden tener costes.

No realizar pruebas de pago hasta tener un presupuesto y autorización.

---

## 3. Qué aporta `gemini-api` y qué no

Documentación: <https://aistudio.google.com/docs/cli>  
Repositorio: <https://github.com/google-gemini/gemini-api-cli>

La CLI `gemini-api` es una CLI stateless, de dependencias mínimas/cero según la documentación, para usar Interactions API desde terminal, scripts o pipelines. Puede:

- generar texto, imágenes, voz, música y vídeo;
- analizar documentos y archivos;
- ejecutar y gestionar Managed Agents (`agent run`, `status`, `cancel`, eliminación de interacciones);
- crear, listar, consultar y eliminar definiciones de custom agents;
- administrar entornos, credenciales, webhooks y triggers;
- entregar salida estructurada JSON/TOON, aplicar `--jq`, ejecutar `--dry-run` y mostrar esquemas.

La documentación la declara beta/experimental y advierte que puede haber breaking changes. En automatizaciones hay que fijar una versión y no depender de `main` o `latest` sin control.

La CLI es stateless por defecto: no mantiene silenciosamente el historial de nuestra aplicación. Las cadenas de trabajo deben utilizar explícitamente IDs de interacción, referencias de ficheros o IDs de background. Esto no impide que Interactions API mantenga estado server-side cuando se usa `previous_interaction_id`; significa que la CLI local no administra por sí sola toda nuestra experiencia de producto.

Instalación documentada para macOS/Linux (NO ejecutar todavía):

```bash
curl -fsSL https://raw.githubusercontent.com/google-gemini/gemini-api-cli/main/scripts/install.sh | bash
```

Configuración de API key documentada mediante variable `GEMINI_API_KEY` o keychain del sistema:

```bash
gemini-api configure --api-key "..."
gemini-api whoami
gemini-api generate "Confirm CLI setup in one sentence." --dry-run
```

No ejecutar estas instrucciones durante el diagnóstico sin autorización. Inspeccionar release, arquitectura soportada y versión adecuada al sistema local.

**Preguntas que debe resolver el análisis:**

- ¿La CLI oficial puede enviar toda la configuración necesaria para ejecutar Antigravity Agent con sources, environment ID, tools, MCP y custom agent?
- ¿Puede ejecutar callbacks de custom functions locales o solo declarar funciones y devolver llamadas al caller?
- ¿Puede mantener un ciclo completo de function calling con herramientas locales de Engram sin escribir un controlador propio?
- ¿Qué flags faltan y se pueden cubrir con `--body`?
- ¿Cómo gestiona estado, streaming, background, reanudación, archivos, snapshot, errores y autenticación?
- ¿Tiene binarios para el Mac y arquitectura reales del usuario? Comprobar releases y checksum.
- ¿Conviene envolverla como subprocess o utilizar directamente el SDK oficial `google-genai` (`google-genai` para Python / `@google/genai` para JS/TS / `google.golang.org/genai` para Go)?

No asumir que `gemini-api` puede ejecutar una función local solo porque la API soporte function calling. Demostrarlo con documentación y/o un test aislado si se autoriza.

---

## 4. Compatibilidad real de Engram con Antigravity Agent remoto

Este es uno de los puntos críticos de la investigación.

### 4.1. Diferencia de transportes

- El MCP normal de Engram se inicia por `stdio` (`engram mcp`).
- Antigravity Agent documenta MCP remoto por Streamable HTTP.
- Por tanto, no hay que declarar que se puede conectar directamente `engram mcp` a Antigravity Agent remoto sin una adaptación.
- Engram también tiene una API HTTP local (`engram serve`) con operaciones de memoria. Esa API JSON no es automáticamente un servidor MCP Streamable HTTP.

Investiga los endpoints reales y sus contratos actuales en `DOCS.md` y el código. No generalices por el nombre "HTTP API".

### 4.2. Estrategia A: custom functions ejecutadas en el Mac (prioridad de investigación)

Arquitectura potencial:

```text
CLI propia en el Mac
  ├── cliente de Gemini Interactions API
  ├── ejecutor de custom functions (en el propio Mac)
  │     └── Engram local (CLI/API/SQLite a través de sus interfaces soportadas)
  ├── estado local de conversación y Environment IDs
  └── transferencia/checkout del proyecto
           │
           └── Interactions API
                    │
                    └── Antigravity Agent en sandbox Linux de Google
```

Flujo conceptual:

1. La CLI llama a Antigravity Agent con `function` tools cuyas descripciones/esquemas representan operaciones permitidas de Engram.
2. Antigravity Agent devuelve una llamada a una función con `requires_action`.
3. La CLI local valida argumentos y permisos, ejecuta la operación mediante la interfaz soportada de Engram local y devuelve el resultado a través del flujo stateful de Interactions API.
4. El agente remoto continúa con la información devuelta.

Ventaja potencial: no se necesita abrir el servidor de Engram al exterior; la ejecución del tool call la realiza el proceso local que controla el usuario.

Comprobar:

- secuencia exacta de `requires_action`, `function_call`, `function_result` y `previous_interaction_id`;
- si hay que volver a declarar las mismas tools/system instructions en cada turno (la documentación de Interactions API indica que algunas opciones son específicas de cada interacción);
- tratamiento de múltiples llamadas y errores;
- timeouts/reintentos/idempotencia;
- cómo mantener la CLI activa mientras el agente solicita callbacks;
- límites de la API y efecto en latencia/coste;
- cómo mapear proyecto, sesión y ownership de Engram sin permitir que el modelo invente un proyecto o cambie el propietario;
- qué operaciones de memoria debe poder ejecutar el agente y cuáles deben reservarse al usuario/admin.

Esta opción parece prometedora, pero no darla por viable hasta entender la API completa y probarla con un almacén Engram temporal.

### 4.3. Estrategia B: gateway MCP remoto

Arquitectura potencial:

```text
Antigravity Agent remoto
    │ MCP Streamable HTTP / HTTPS
    ▼
Gateway MCP autenticado
    │ API controlada
    ▼
Engram
```

Investigar alternativas:

- gateway que traduzca MCP remoto a operaciones de la API local de Engram;
- adaptador que envuelva las operaciones de Engram y las exponga como MCP Streamable HTTP;
- servicio remoto junto a una instancia de Engram, manteniendo Git Sync/Cloud para replicar con el nodo local;
- túnel/reverse proxy con conexión saliente iniciada desde el Mac, si técnicamente resulta necesario.

No exponer directamente `engram serve` a Internet. La documentación de Engram describe un modelo local para clientes/procesos de confianza; `ENGRAM_HTTP_TOKEN` no debe considerarse una capa universal de aislamiento/autorización de todas las rutas. Si se propone gateway, elaborar threat model, autenticación, autorización por proyecto, allowlist de operaciones, TLS, rate limiting, logs sin secretos, rotación de credenciales y recuperación.

### 4.4. Estrategia C: ejecutar Engram dentro del sandbox remoto

Evaluar si el sandbox puede instalar el binario Linux de Engram y utilizar su CLI localmente contra SQLite dentro del mismo Environment ID.

Ventajas potenciales:

- no hay que exponer servicios locales;
- el agente puede invocar `engram` desde Bash;
- los archivos/paquetes persisten al reutilizar el mismo environment ID, según Google.

Riesgos y preguntas:

- ¿cuánto dura realmente el entorno y qué garantías de persistencia tiene?
- ¿qué ocurre si Google elimina/recicla el entorno?
- ¿cómo se sincroniza el almacén con el Engram local del usuario?
- ¿Git Sync puede exportar/importar los chunks entre entornos sin corrupciones/conflictos?
- ¿se puede instalar un binario compatible con la arquitectura/Ubuntu del sandbox?
- ¿qué diferencia hay entre tener SQLite remoto y la fuente de verdad local-first deseada?
- ¿cómo manejar proyectos múltiples y credenciales?

No adoptar esta arquitectura antes de demostrar persistencia, exportación, recuperación y coherencia.

### 4.5. Estrategia D: Git Sync como memoria portable

Engram puede exportar memoria a `.engram/` en formato de manifest y chunks. Evaluar si la CLI puede usar este mecanismo como puente entre Mac y entorno remoto mediante GitHub.

Aclarar que esto no equivale automáticamente a memoria interactiva en tiempo real. Estudiar:

- frecuencia de sync;
- conflictos y ownership;
- exclusiones de datos sensibles;
- concurrencia de dos entornos;
- si conviene exportar prompts o excluirlos;
- cómo evitar commits accidentales de información privada.

No inventar ni implementar un formato alternativo si Engram ya tiene un contrato oficial.

---

## 5. Compatibilidad real de Gentle-AI con Antigravity Agent remoto

No tratar Gentle-AI como un único bloque indivisible. Separar cada componente según su portabilidad.

### Probablemente portable/adaptable

- ODD como protocolo/instrucciones en `AGENTS.md`.
- Skills en `.agents/skills/<nombre>/SKILL.md`, adaptando rutas y referencias.
- Persona e instrucciones de orquestación.
- Parte del protocolo de memoria de Engram.
- Convenciones de exploración, autorización, verificación y handoff.
- Documentos de feature/tareas como archivos del workspace remoto.

### Requiere adaptación técnica o integración específica

- RDD: no reducirlo a un prompt de revisión. Estudiar su CLI, estados, ledger, congelación del candidato, evidencia y autoridad. Determinar qué parte puede ejecutarse dentro del sandbox y cuál debería controlar el cliente local.
- CodeGraph: averiguar dependencias, requisitos de indexación/árbol de símbolos y viabilidad en el Ubuntu remoto.
- Context7: comprobar el transporte MCP y cómo conectar el servidor desde Antigravity Agent.
- subagentes/revisores: comprobar las capacidades disponibles en este harness/API y si existe delegación nativa equivalente. No asumir que los subagentes de cada host están disponibles.
- guardrails/permisos: las protecciones escritas o configuraciones de un IDE no se trasladan automáticamente al sandbox remoto. Distinguir instrucciones del modelo de controles técnicos aplicados por Google, por el cliente o por el servidor.

### No asumir que funciona

- `gentle-ai install --agent antigravity` dentro del sandbox o desde el Mac como método que configura automáticamente todo en un agente remoto administrado.
- hooks de ciclo de vida del IDE local;
- permisos de filesystem del cliente local;
- rutas globales `~/.gemini/...` del Mac como rutas del sandbox;
- agentes personalizados instalados para Antigravity IDE como agentes personalizados de `antigravity-preview-09-2026`.

La solución probable podría ser un **adaptador de runtime**: reutilizar los contratos, instrucciones, skills y lógica pertinente de Gentle-AI, pero instalar/preparar los archivos adecuados para `.agents/` y ejecutar las operaciones necesarias en el contexto de Interactions API.

Hay que distinguir claramente "portamos instrucciones" de "portamos el producto y todas sus garantías".

---

## 6. Las dos rutas principales que debemos comparar

### Ruta 1 — Aprovechar el stack oficial de Google con adaptación mínima

Posible composición:

- Antigravity Agent como runtime remoto.
- Interactions API como interfaz de ejecución y continuidad.
- entornos remotos y Environment IDs para workspace/sesiones.
- `.agents/AGENTS.md` para instrucciones de ODD y protocolo de memoria.
- `.agents/skills/` para skills seleccionadas de Gentle-AI y Google.
- Gemini Docs MCP para documentación actual de Gemini.
- Gemini Agent Skills para patrones oficiales de Gemini API.
- custom functions locales o gateway MCP remoto para Engram.
- GitHub/GCS/snapshots para transportar código y artefactos.
- AI Studio Playground para prototipar/configurar visualmente cuando aporte valor.
- AI Studio Build para crear/previsualizar apps y sincronizarlas con GitHub, cuando el caso sea desarrollo de apps.

Ventajas que hay que comprobar:

- máxima reutilización de infraestructura;
- menor cantidad de código propio;
- herramientas remotas ya gestionadas;
- entorno aislado de Google;
- actualizaciones de capacidades bajo responsabilidad de Google.

Limitaciones que hay que comprobar:

- dependencia de cuotas, precios, API preview y cambios de Google;
- disponibilidad real de todas las funciones vía API frente a UI;
- control limitado del entorno/runtime;
- acceso al filesystem local indirecto;
- persistencia del sandbox y retención de interacciones;
- integración no nativa de Engram/Gentle-AI;
- falta de una experiencia CLI completa con memoria, sesiones y repositorios locales.

### Ruta 2 — Construir una CLI propia sobre la infraestructura Google

No crear un nuevo modelo ni reimplementar el harness. Crear una capa local que coordine las APIs/CLIs oficiales, archivos del proyecto, memoria y metodología.

Componentes potenciales:

1. **CLI UX:** comandos, salida legible, modo interactivo, streaming, JSON, diagnósticos.
2. **Google backend adapter:** Interactions API y/o `gemini-api` oficial.
3. **Environment manager:** crear/reutilizar/inspeccionar entornos, sources, Environment IDs, snapshots y descargas.
4. **Conversation/session manager:** almacenar localmente IDs, historial operativo, relación conversación↔proyecto↔Environment ID, reanudación y cancelación.
5. **Project/workspace adapter:** preparar proyecto local, GitHub branch/sync, uploads, descarga y conciliación de cambios.
6. **Instruction/skill pack manager:** componer `AGENTS.md`, seleccionar skills, evitar duplicados y versionar los assets.
7. **Engram adapter:** ejecutar herramientas de memoria de forma local y segura (custom function calling, MCP gateway u otra estrategia demostrada).
8. **Gentle-AI adapter:** aplicar ODD, skills, RDD y componentes que sean realmente portables; conservar la semántica cuando no se pueda portar.
9. **Policy/security layer:** autorización, operaciones de memoria permitidas, red, secretos, paths, acciones destructivas y confirmaciones.
10. **Diagnostics/telemetry:** errores, costes/uso disponibles, IDs, trazas, versiones y recuperación.

Ejemplos de comandos de producto (solo ideas, no decisión de diseño):

```text
<cli> init
<cli> agent run
<cli> agent resume
<cli> agent status
<cli> agent cancel
<cli> env create
<cli> env inspect
<cli> env snapshot
<cli> project attach
<cli> project sync
<cli> memory doctor
<cli> config validate
```

No diseñar una CLI enorme sin comprobar qué ofrece ya `gemini-api`. Identificar exactamente qué es necesario añadir, qué conviene envolver y qué no conviene mantener.

### Alternativa intermedia — `gemini-api` + configuración portable + adaptador Engram

Evaluar expresamente una tercera opción: utilizar la CLI oficial de Google como motor de ejecución desde la terminal y añadir solo un pequeño wrapper/dispatcher para estado del proyecto, configuración de skills y callbacks de memoria.

Puede que esta opción dé casi todo el valor de una CLI propia con mucha menos superficie de mantenimiento.

---

## 7. Stack técnico: no decidir antes de comparar

Investiga al menos estas opciones:

- **Go:** compatible con el ecosistema de Engram y con el lenguaje de la CLI oficial de Google si su implementación actual es Go; SDK oficial disponible. Buena opción para una CLI distribuible y portable.
- **TypeScript/Node.js:** SDK oficial `@google/genai`, buen ecosistema de CLI y compatibilidad con el workflow del usuario (pnpm). Comprobar empaquetado multiplataforma y dependencias.
- **Python:** SDK `google-genai`, rápido para prototipos y tool dispatch; evaluar distribución, entorno virtual y mantenimiento.

Comparar:

- SDK actual y soporte de Interactions API;
- compatibilidad con macOS Intel y Apple Silicon, Linux y Windows;
- facilidad para invocar Engram local;
- streaming y manejo de function calling;
- empaquetado, actualizaciones y firma;
- testabilidad y fiabilidad;
- coste de mantenimiento y dependencia de librerías.

El usuario ha utilizado pnpm para proyectos Node; no sustituirlo por npm sin motivo. Verificar la arquitectura real del ordenador antes de decidir los binarios.

---

## 8. Seguridad, privacidad y operación: requisitos no negociables

1. **No exponer Engram local sin un diseño de seguridad real.** La API local de Engram está orientada a clientes/procesos confiables. Un token opcional que protege determinadas rutas no debe interpretarse como autorización completa para un servicio público.
2. **Preferir el callback local de custom functions si funciona bien.** El cliente local recibe la solicitud de herramienta y ejecuta Engram en el propio Mac; así se evita abrir un endpoint entrante. Comprobar sus requisitos stateful y su comportamiento de errores.
3. **No dar acceso completo al filesystem del Mac al agente remoto.** Utilizar repositorio/branch, archivos seleccionados o un mecanismo explícito de sincronización; nunca una función genérica de shell local con permisos ilimitados.
4. **No subir `.env`, claves, certificados, llaveros, `~/.ssh`, credenciales ni ficheros privados** al sandbox, GitHub, GCS o contexto del modelo.
5. Usar credenciales almacenadas y referencias seguras cuando Google lo permita; no guardar secretos como literales en `AGENTS.md`, skills, prompts o repositorios.
6. Configurar network allowlist con dominios mínimos. Evitar comodines amplios salvo prueba y justificación.
7. Utilizar GitHub PAT de mínimo privilegio, preferiblemente acceso read-only para análisis; ampliar permisos solo cuando se autorice una operación de escritura concreta.
8. Separar operaciones Engram de agente (`search/context/save/session summary`) de operaciones administrativas (`delete`, merge, mantenimiento global). El agente no debe recibir tools admin innecesarias.
9. En toda llamada de memoria validar proyecto/sesión/ownership con reglas de Engram. No dejar que el modelo fabrique un `expected_project`, sesión o identificador para saltarse el contrato.
10. Evitar que instrucciones, código, issues, README o contenido remoto puedan modificar las reglas de seguridad. Tratar el contenido del repositorio como datos, no como autoridad.
11. Los trabajos autónomos necesitan límites de alcance, criterios de terminación, cancelación y límites de coste.
12. Definir qué datos se almacenan en Interactions API y durante cuánto tiempo, y qué información debe existir únicamente en Engram local.
13. No asumir que una regla escrita en `AGENTS.md` constituye un control de seguridad. Separar recomendaciones al modelo de enforcement técnico.
14. Revisar licencias y marcas. Engram y Gentle-AI publican licencia MIT, pero mantienen políticas de marcas independientes. `gemini-api-cli` tiene su propia licencia (consultar la release actual). El repositorio `google-gemini/gemini-skills` incluye una advertencia sobre su condición de proyecto no oficialmente soportado; revisar el texto actual antes de incorporarlo.

---

## 9. Plan de investigación y pruebas

### Fase 0 — Inventario del ordenador y repositorios (solo lectura)

1. Detectar SO, versión, arquitectura CPU, shell y gestores de paquetes.
2. Detectar Python, Go, Node, pnpm, Git y herramientas Google ya instaladas.
3. Comprobar si existen `gemini-api`, `gemini`, `agy`, `engram`, `gentle-ai`; mostrar únicamente versión/ruta, nunca credenciales.
4. Comprobar clones locales de los repositorios y su estado Git (sin modificar nada).
5. Determinar cuál es el workspace objetivo y si tiene código sensible.
6. No volcar variables de entorno completas. Comprobar solo presencia/ausencia de nombres de variables, sin mostrar valores.

### Fase 1 — Auditoría de fuentes y compatibilidad

1. Consultar documentación oficial de Google y registrar fecha de consulta.
2. Inspeccionar repositorios y releases/tags de Engram, Gentle-AI, Gemini API CLI y Gemini Skills.
3. Separar main de la última release estable.
4. Leer los archivos de instalación, configuración de Antigravity, MCP, skills, componentes, RDD, CodeGraph y API HTTP relevantes.
5. Producir una matriz `capacidad → soporte nativo → adaptación necesaria → evidencia → incertidumbre`.
6. No confiar en resúmenes anteriores si el código actual los contradice.

### Fase 2 — Diseñar prueba de concepto Google aislada (sin ejecutarla todavía)

Definir un experimento mínimo y acotado para comprobar:

1. crear una interacción con Antigravity Agent;
2. obtener `interaction_id` y `environment_id`;
3. escribir y leer un archivo de prueba en el sandbox;
4. continuar en el mismo environment ID y verificar que el archivo/paquete persiste;
5. probar `previous_interaction_id`;
6. probar background/status/cancel;
7. probar descarga/snapshot del entorno;
8. probar mount de un repositorio GitHub de prueba read-only;
9. medir consumo y latencia disponibles;
10. determinar qué sucede al crear entorno nuevo, reutilizarlo y descargarlo.

Preparar los comandos/código exactos, prerequisitos, coste estimado y criterio de éxito. Esperar permiso antes de ejecutarlo.

### Fase 3 — Prueba de concepto de Engram, sin tocar la memoria real

Diseñar una prueba basada en un directorio de datos temporal y un proyecto ficticio:

- llamada de memoria actual;
- búsqueda contextual;
- guardado de una memoria de prueba;
- recuperación completa;
- resumen de sesión;
- rechazo de una petición que intente escribir en otro proyecto;
- verificación de que la memoria local sigue íntegra.

Comparar primero:

A. custom functions ejecutadas por la CLI local;  
B. gateway MCP remoto Streamable HTTP;  
C. Engram ejecutado en el sandbox con Git Sync;  
D. alternativa basada en API HTTP/CLI si existe una vía soportada mejor.

No usar la base de datos real del usuario ni datos reales en la primera POC.

### Fase 4 — Prueba de adaptación de Gentle-AI

Seleccionar un subconjunto pequeño:

- un `AGENTS.md` basado en ODD;
- dos skills realmente útiles;
- protocolo de Engram;
- una tarea de ejemplo pequeña y otra sustancial.

Evaluar si el agente sigue autorización → exploración → implementación → comprobaciones, si evita planificar de más, si registra progreso y si deja un handoff recuperable.

Para RDD, realizar primero un análisis de su implementación real. No proclamar paridad solo por haber copiado sus instrucciones.

### Fase 5 — Comparativa de arquitectura y decisión

Comparar:

- Ruta 1: Managed Agent configurado en AI Studio.
- Ruta 2: Gemini API CLI oficial + configuración portable + adaptadores mínimos.
- Ruta 3: CLI propia con SDK oficial.
- Ruta 4: Antigravity local/CLI tradicional como alternativa o complemento.

Puntuar del 1 al 5 con evidencia en:

- capacidades;
- integración Engram;
- integración Gentle-AI;
- acceso al proyecto local;
- persistencia/reanudación;
- seguridad;
- privacidad;
- coste;
- facilidad de uso;
- mantenimiento;
- independencia de cambios de Google;
- portabilidad.

La recomendación debe explicar qué se reutiliza, qué se adapta, qué se desarrolla y qué se descarta.

---

## 10. Preguntas técnicas que el informe debe responder expresamente

### Google / Antigravity

1. ¿Qué puede configurarse en el Playground UI y qué solo mediante Interactions API?
2. ¿Se puede guardar un custom agent y definir un agente propio con instrucciones, tools, skills y configuración de entorno?
3. ¿Qué diferencias reales hay entre agent ID, custom agent, interaction ID y environment ID?
4. ¿Qué se conserva en el Environment ID? ¿durante cuánto tiempo? ¿qué límites o garantías de disponibilidad tiene?
5. ¿Qué partes del harness del IDE se comparten y cuáles no están expuestas al agente administrado?
6. ¿Se puede mantener una interacción conversacional larga, continuar después de cerrar la CLI y retomar el mismo sandbox?
7. ¿Cómo funcionan streaming, background, cancelación, errores y timeouts?
8. ¿Qué modelos y opciones de `agent_config` están disponibles hoy?
9. ¿Qué precios/cuotas aplican al agente y a las llamadas Gemini que el propio sandbox pudiera ejecutar?
10. ¿Qué límites de archivos, fuentes, red, tiempo y recursos tiene el sandbox?

### Gemini API CLI

11. ¿Qué cubre realmente `gemini-api` y qué queda fuera?
12. ¿Es estable o beta/experimental? ¿qué versión fijar?
13. ¿Cómo se crean y versionan custom agents?
14. ¿Qué campos de entorno, fuentes, MCP, funciones, variables y credenciales se soportan desde la CLI?
15. ¿Puede el CLI oficial ejecutar callbacks locales de custom functions? Si no, ¿cómo se haría con SDK sin duplicar demasiado código?
16. ¿Cómo se guarda y restaura el estado necesario para `agent run`, `status`, `resume` y `cancel`?
17. ¿Qué soporte binario existe para la arquitectura detectada del ordenador?

### Engram

18. ¿Qué interfaces actuales son estables y cuáles son internas?
19. ¿La API HTTP contiene todos los endpoints necesarios para las operaciones de agente?
20. ¿Qué transportes MCP implementa el binario actualmente?
21. ¿Qué seguridad real aplica cada ruta de HTTP?
22. ¿Cómo se asigna el proyecto y se comprueba ownership?
23. ¿Qué estrategia local→remoto preserva mejor el modelo local-first?
24. ¿Cómo pueden resolverse sesiones de Engram cuando cada llamada remota usa interaction IDs y environment IDs de Google?
25. ¿Qué ocurre con compaction, nuevas conversaciones, nuevas máquinas y pérdida de un environment ID?

### Gentle-AI

26. ¿Qué componentes de la última release son portables como instrucciones/skills?
27. ¿Qué componentes dependen de hooks, subagentes, procesos locales o rutas de host?
28. ¿Qué parte de RDD puede reutilizarse sin alterar su garantía de revisión del candidato exacto?
29. ¿Se puede reutilizar CodeGraph dentro del sandbox?
30. ¿Cómo se conserva la seguridad de paths, operaciones y secretos en un entorno remoto?
31. ¿Cómo evitar duplicar skills o cargar instrucciones incompatibles entre `.agents/`, Engram y Gentle-AI?

### Arquitectura propia

32. ¿Qué hace ya el CLI oficial y qué aportaría realmente nuestro CLI?
33. ¿Cuál es el mínimo código propio necesario para tener una CLI útil?
34. ¿Cuál de Go, TypeScript o Python reduce más complejidad y riesgo?
35. ¿Cuál es la forma más segura de ejecutar las herramientas de Engram localmente sin exponer servicios?
36. ¿Cómo se suben y recuperan cambios de proyectos grandes sin perder cambios concurrentes?
37. ¿Cómo separar memoria, historial conversacional, estado del sandbox y estado Git para no confundirlos?
38. ¿Qué pasaría si Google cambia o retira el agente preview? ¿podemos cambiar el backend sin perder memoria ni flujo de trabajo?

---

## 11. Criterios para considerar que una solución es realmente buena

No basta con que el agente responda a un prompt. Para considerar viable la solución, debe demostrar:

- **Memoria real:** puede recuperar una decisión de otra interacción, no solo recordar el chat actual.
- **Aislamiento de proyectos:** una búsqueda o escritura no mezcla proyectos y no permite cambiar ownership arbitrariamente.
- **Reanudación fiable:** se puede continuar el trabajo tras cerrar la CLI, perder la terminal o compactar contexto.
- **Estado separado:** conversación Google, entorno remoto, workspace Git y memoria Engram tienen identidades y ciclos de vida explícitos.
- **Código recuperable:** el resultado del trabajo remoto llega al repositorio local de forma verificable y sin sobrescribir cambios concurrentes.
- **Metodología efectiva:** ODD se sigue sin convertir tareas pequeñas en procesos burocráticos.
- **Revisión demostrable:** cualquier afirmación de RDD se apoya en el mecanismo real y en el candidato exacto.
- **Seguridad:** ningún secreto ni filesystem privado queda expuesto al sandbox sin autorización específica.
- **Coste controlable:** las tareas tienen límites, cancelación y trazabilidad de uso.
- **Mantenibilidad:** se reutiliza el máximo de Google, Engram y Gentle-AI sin duplicar sus responsabilidades.
- **Portabilidad:** la memoria y los archivos del proyecto no quedan atrapados en un Environment ID de Google.

---

## 12. Entregable esperado del agente local

Entrega un informe en español con esta estructura:

1. **Resumen ejecutivo:** qué es viable y qué no, con recomendación preliminar.
2. **Inventario del entorno local:** SO, arquitectura, herramientas y repositorios detectados, sin secretos.
3. **Fuentes y versiones:** links, ramas/tags/commits y fecha de comprobación.
4. **Mapa de capacidades Google:** Build, Playground Managed Agents, Antigravity Agent, Interactions API y `gemini-api`.
5. **Matriz Engram:** interfaz, transporte, endpoints, persistencia, proyecto/ownership, compatibilidad.
6. **Matriz Gentle-AI:** componentes portables, componentes que requieren adaptación y componentes no trasladables.
7. **Diseños alternativos:** mínimo tres arquitecturas, diagramas y flujo de datos.
8. **Análisis de custom function calling local vs MCP remoto vs Engram dentro del sandbox.**
9. **Comparativa de utilizar herramientas oficiales vs CLI propia.**
10. **Riesgos, privacidad, seguridad, costes, limitaciones y dependencia de Google.**
11. **Pruebas de concepto propuestas:** código/comandos, prerequisitos, coste potencial, criterio de éxito y rollback; no ejecutar sin autorización.
12. **Recomendación:** ruta preferida y razones, ruta alternativa y plan incremental.
13. **Preguntas abiertas:** solo las que bloquean decisiones reales.

Cada afirmación técnica importante debe ir acompañada de una fuente oficial, ruta de código, versión o prueba. Distingue claramente:

- hecho comprobado;
- documentación oficial pero no probado localmente;
- hipótesis técnica;
- recomendación;
- incompatibilidad demostrada;
- cuestión pendiente.

No presentes una idea como integración nativa si requiere un adaptador propio. No uses como evidencia solo un README cuando el comportamiento depende del código.

---

## 13. Fuentes principales de referencia

### Google AI Studio / Gemini API

- Build mode: <https://aistudio.google.com/docs/aistudio-build-mode>
- Agents in AI Studio Playground: <https://aistudio.google.com/docs/aistudio-agents>
- Antigravity Agent: <https://aistudio.google.com/docs/antigravity-agent>
- Managed Agents Quickstart: <https://aistudio.google.com/docs/managed-agents-quickstart>
- Environments: <https://aistudio.google.com/docs/agent-environment>
- Interactions API: <https://aistudio.google.com/docs/interactions-overview>
- Coding agents: <https://aistudio.google.com/docs/coding-agents>
- Gemini API CLI: <https://aistudio.google.com/docs/cli>
- Custom agents: <https://aistudio.google.com/docs/custom-agents>
- Credentials: <https://aistudio.google.com/docs/agent-credentials>
- API keys: <https://aistudio.google.com/apikey>
- Gemini API CLI repository: <https://github.com/google-gemini/gemini-api-cli>
- Gemini Skills repository: <https://github.com/google-gemini/gemini-skills>
- Managed Agent templates: <https://github.com/google-gemini/gemini-managed-agents-templates>
- Gemini Docs MCP: <https://gemini-api-docs-mcp.dev>

### Engram

- Repository: <https://github.com/Gentleman-Programming/engram>
- Agent setup: <https://github.com/Gentleman-Programming/engram/blob/main/docs/AGENT-SETUP.md>
- Full reference: <https://github.com/Gentleman-Programming/engram/blob/main/DOCS.md>
- Architecture: <https://github.com/Gentleman-Programming/engram/blob/main/docs/ARCHITECTURE.md>
- HTTP workflows: <https://github.com/Gentleman-Programming/engram/blob/main/docs/HTTP-AGENT-WORKFLOWS.md>
- Cloud: <https://github.com/Gentleman-Programming/engram/blob/main/docs/engram-cloud/README.md>

### Gentle-AI

- Repository: <https://github.com/Gentleman-Programming/gentle-ai>
- Intended usage: <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/intended-usage.md>
- Usage/ODD/RDD: <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/usage.md>
- Agent integrations: <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/agents.md>
- Components: <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/components.md>
- Engram integration: <https://github.com/Gentleman-Programming/gentle-ai/blob/main/docs/engram.md>

---

## 14. Instrucción final al agente

Aborda esto como una revisión de arquitectura antes de implementar. Investiga en profundidad, cuestiona las suposiciones, detecta contradicciones entre documentación y código, y no confundas capacidades del Antigravity IDE local con las del Antigravity Agent administrado.

**La decisión que buscamos no es “qué paquete instalo”, sino cuál es la arquitectura más simple y robusta para combinar el runtime administrado de Google con la memoria y metodología de Engram y Gentle-AI, conservando acceso seguro al proyecto local y evitando reimplementar capacidades que Google ya ofrece.**

Primero devuelve el diagnóstico y las alternativas. No escribas código de producto ni modifiques configuraciones hasta recibir autorización explícita.
