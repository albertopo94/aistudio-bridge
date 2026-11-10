he encontrado una precisión importante en el punto 4: sustituir HOME y limpiar las variables de entorno no equivale a aislar un proceso del sistema de archivos del Mac. Te explicaré por qué antes de recomendarte pasar a la siguiente arquitectura.
1. Revisión de los nueve cambios
Esta es mi evaluación del código actual, no solamente de los mensajes de los commits.
#	Cambio	Mi valoración
1	Extracción segura de TAR	Resuelto
2	Entregas limpias y eliminaciones	Resuelto y verificado
3	Memoria de solo lectura	Resuelto
4	No ejecutar tests automáticamente	Parcial
5	continue entrega el código	Resuelto
6	IDs únicos	Resuelto
7	Recuperación tras interrupciones	Parcial
8	Corrección de ask --stream	Resuelto
9	Mostrar errores de tests	Resuelto

1. Extracción segura de TAR
La solución es buena.

En sync.py se comprueban las rutas antes de extraer, se rechazan enlaces simbólicos, enlaces duros, dispositivos y rutas que escapen del directorio. También existe un límite de 4 GiB para el tamaño descomprimido declarado.

Me gusta especialmente que se haya conservado la compatibilidad con Python 3.10 sin recuperar el antiguo extractall() desprotegido. La extracción manual de archivos regulares es mucho mejor que desactivar las protecciones.

Una limitación que conviene conocer: rechazar todos los enlaces simbólicos también puede impedir la entrega de un repositorio legítimo que los utilice. No es un fallo de seguridad; es una restricción de compatibilidad que debería tener pruebas específicas y un mensaje comprensible.
2. Entregas limpias y eliminaciones reales
Este cambio ha mejorado bastante con respecto a la primera versión.

En apply_to_branch() ya se calcula qué archivos contiene realmente el workspace entregado. Después:
- Se eliminan los archivos rastreados que el agente ha borrado.
- Se copian los nuevos y modificados.
- Se preparan exactamente las rutas que pertenecen al workspace.
- Se crea un commit de entrega.
Hay un detalle que considero especialmente positivo: la corrección posterior del verificador independiente.

La primera solución todavía permitía que git add -A sobre una carpeta rastreada incorporase un archivo untracked que el usuario hubiese dejado dentro de ella. El commit b18cdbf corrigió precisamente ese caso.

Esto es buena ingeniería: no dar por terminada una tarea simplemente porque las pruebas escritas por quien implementa han pasado.
3. Memoria de solo lectura por defecto
También está bien resuelto para el uso actual.

MemoryDispatcher solo anuncia memory_save cuando se ha activado --memory-save. Además, valida los datos y limita el título a 200 caracteres y el contenido a 8.000.

Es una buena frontera de seguridad: el agente remoto no recibe permiso de escritura persistente simplemente por poder consultar Engram.

Hay, sin embargo, un detalle que debemos corregir antes de convertir el bridge en un servicio concurrente: allow_save es una propiedad mutable del dispatcher compartido. Dos tareas simultáneas podrían modificar el permiso de la otra. Lo explico más adelante.
4. Tests sin ejecución automática
Aquí no daría el punto por completamente solucionado.

El código actual hace tres cosas acertadas:
- No ejecuta tests por defecto.
- Requiere --run-tests.
- Construye un entorno de variables por allowlist y sustituye HOME por un directorio temporal.
Puedes verlo en test_env() y run_tests().

Pero hay una diferencia fundamental:

Cambiar HOME y eliminar variables de entorno no aísla el sistema de archivos.

El proceso se ejecuta con el mismo usuario del Mac. Por tanto, si las credenciales están protegidas mediante permisos de usuario, ese proceso puede tener acceso a ellas. Un código malicioso puede intentar leer rutas absolutas como:

/Users/tu_usuario/.ssh/
/Users/tu_usuario/.remote-worker/config.json
/Users/tu_usuario/...

Tampoco se establece una restricción de red para el proceso de tests.

Esto es especialmente relevante porque el archivo de configuración del bridge puede contener la clave de Google.

La decisión de convertir --run-tests en una acción explícita es correcta. Lo que no aceptaría es describir esa ejecución como un sandbox sin acceso a secretos.

Mi recomendación: mantener la ejecución desactivada por defecto y, para una verdadera ejecución aislada, introducir un contenedor o una máquina virtual con un filesystem restringido, sin credenciales y con red desactivada por defecto.
5. continue entrega el código
Resuelto.

La operación _deliver() es compartida por run() y continue_task(). Es una mejora arquitectónica importante porque evita mantener dos procesos de entrega que puedan divergir.

Referencia: runner.py y continue_task().
6. IDs únicos
La solución es suficiente para el volumen de uso habitual.

Ahora se utiliza una marca temporal acompañada de un sufijo aleatorio UUID. Además, la creación del registro comprueba que no se esté fusionando accidentalmente con otra interacción existente.

Referencias: state.py y TaskStore.record().

Un matiz: el sufijo utiliza solamente ocho caracteres hexadecimales, es decir, 32 bits aleatorios. No es una garantía matemática de unicidad. Yo utilizaría el UUID completo cuando el bridge pase a gestionar más tareas simultáneas. La protección contra sobrescrituras ya es un buen segundo mecanismo.
7. Recuperación tras interrupciones
La mejora es real, pero el problema no está completamente cerrado.

run_until_complete() guarda la nueva interacción después de cada ronda de llamadas a funciones.

Eso resuelve el caso principal: el proceso se interrumpe entre rondas y el registro conserva el identificador más reciente.

Pero quedan dos ventanas de recuperación:

Primera: una función local puede ejecutarse y producir un efecto persistente —por ejemplo, memory_save— y el proceso puede morir antes de enviar su resultado a Google. Al continuar, la llamada puede ejecutarse otra vez. No hay todavía una garantía general de ejecución exactamente una vez.

Segunda: Google puede haber completado la interacción, pero el proceso local puede morir antes de registrar ese estado. En ese caso, continue_task() consulta la interacción, pero si ya está completada no recupera directamente su resultado: continúa creando otra interacción con la instrucción continue.

Yo añadiría una reconciliación explícita:
- Si Google dice que sigue ejecutándose, recuperar seguimiento.
- Si dice que está completada, recuperar el resultado y ejecutar _deliver() directamente.
- Si dice que ha fallado, registrar el estado terminal.
- Si hay una llamada local pendiente, controlar su posible repetición mediante idempotencia.
Esto hará que la recuperación sea realmente robusta.
8 y 9. Streaming y diagnóstico
Los dos están bien resueltos.

ask --stream utiliza ahora las mismas partes de entrada que la opción normal, incluyendo imágenes. Además, informa de que el texto ya se ha enviado para evitar imprimirlo otra vez.

Y _report_run() muestra el output de los tests cuando el código de salida es distinto de cero.

Los commits 28cf074 y b18cdbf recogen estas correcciones.

2. Los problemas adicionales que he encontrado
Esta es la parte más importante de mi revisión. Los nueve cambios han mejorado el puente, pero hay cuestiones que debemos considerar antes de convertirlo en un runtime de Gentle Shell.
A. Ya existe una buena parte de la nueva arquitectura
Importante
Hay algo que debemos actualizar de nuestra conversación anterior: aistudio-bridge ya dispone de una vía para enviar un proyecto local completo a Google sin necesidad de subirlo previamente a GitHub.

Es el comando:

remote-worker run "..." --local-repo /ruta/al/proyecto

La operación archive_repo() construye un archivo con:
- Archivos rastreados.
- Archivos nuevos no ignorados.
- Modificaciones staged y unstaged reflejadas en sus contenidos.
- Exclusión de .git y de archivos ignorados.
Después, _upload_local_repo() utiliza la API de subida reanudable de Google para transferir el archivo.

Referencias: sync.py y runner.py.

Esto es muy relevante: no necesitamos volver a construir desde cero la transferencia de un workspace local a un entorno remoto.

Pero existe una carencia que sí considero importante.

El archivo enviado no incluye .git, por lo que Antigravity recibe el árbol de archivos, pero no recibe la historia Git, la rama original ni el historial de commits locales.

Y hay otro problema más delicado.
B. No se conserva la revisión exacta del proyecto de partida
Prioridad alta
Al iniciar una tarea, se toma una instantánea del proyecto. Sin embargo, al devolver el resultado, apply_to_branch() hace un fetch y reconstruye la rama tomando como base la referencia remota actual.

No he encontrado que se guarde un source_commit, base_sha o identificador equivalente que fije el punto exacto del proyecto que recibió Antigravity.

¿Qué puede ocurrir?
1. Lanzas una tarea desde un proyecto en el commit A.
2. Antigravity comienza a trabajar sobre el contenido de A.
3. Mientras tanto, tú haces otro commit, B, en tu proyecto local, o alguien actualiza la rama GitHub.
4. El agente finaliza.
5. La entrega crea su rama desde B, pero superpone el contenido completo generado sobre A.
En apply_to_branch(), los archivos que no existen en el workspace remoto se pueden interpretar como eliminaciones. Por tanto, incluso podrían desaparecer archivos que alguien añadió en B.

La entrega no modifica directamente tu carpeta original, pero la rama resultante puede incorporar cambios incorrectos o borrar avances realizados mientras el agente trabajaba.

La solución es conservar desde el principio:
- El commit base.
- El árbol o hash de la instantánea.
- El manifiesto de archivos enviados.
- Los cambios locales presentes en el momento de la captura.
Y construir el resultado contra ese punto de partida, con detección de conflictos cuando la rama original haya avanzado.

Referencia: apply_to_branch() y la creación del registro de tarea.
C. El sistema actual no está preparado para ejecutar tareas concurrentes de forma segura
Prioridad alta para Gentle Shell
Este es probablemente el mayor problema arquitectónico que he encontrado pensando en la integración futura.

El bridge actual utiliza un directorio compartido:

~/.remote-worker/<project>/work/clone

Las entregas de las tareas del mismo proyecto utilizan ese clon. También comparten el TaskStore JSON y el MemoryDispatcher.

Esto funciona razonablemente bien en el CLI actual, donde el usuario suele lanzar una operación cada vez.

Pero Gentle Shell gestiona agentes en segundo plano y permite varias tareas simultáneas. Si llamamos al mismo TaskRunner desde un servicio MCP de larga duración, podemos tener:
- Dos entregas modificando el mismo clon.
- Una tarea cambiando la rama mientras otra la está preparando.
- Dos escrituras simultáneas perdiendo actualizaciones en state.json.
- Una tarea que activa allow_save mientras otra no tiene permiso para guardar memoria.
La persistencia atómica del JSON evita archivos parcialmente escritos, pero no evita las condiciones de carrera entre operaciones de lectura-modificación-escritura.

No llevaría el TaskRunner actual directamente a un servidor MCP concurrente sin refactorizar estas responsabilidades.

Mi solución sería:
- Un workspace de entrega independiente por tarea.
- Un estado persistente con escrituras seguras bajo concurrencia.
- Permisos de memoria inmutables y específicos de cada tarea.
- Control explícito de los trabajos activos.
- Un mecanismo para reconciliar y recuperar una tarea sin depender del estado en memoria de un único proceso.
D. La vía de Google Cloud Storage no está terminada
El proyecto ya tiene --gcs-source, y eso está bien. Pero hay una diferencia entre tener construido el mecanismo de entrada y tener el flujo completo funcionando.

En routes.py, el propio proyecto marca la vía GCS como not validated live.

Además, he detectado una incompatibilidad en la entrega:
1. run() asigna la URI gs://... a repo_url.
2. Después de completar el trabajo, llama a _deliver().
3. _deliver() llama a apply_to_branch().
4. Esta última espera clonar un repositorio Git utilizando git clone.
Pero una URI gs:// no es una URL de un repositorio Git.

Por tanto, la vía GCS no tiene actualmente resuelto cómo regresar desde el workspace remoto hasta una rama Git local de forma normal. Puede acabar en undelivered, conservando el sandbox, que es mejor que perder el trabajo, pero no cumple el flujo completo.

No la consideraría una vía lista para producción hasta resolver también esa parte.
E. El retorno de archivos grandes sigue siendo un cuello de botella
El proyecto ha medido algo importante en sus propias pruebas reales:
- Una descarga de un archivo de 1 GiB llegó a completarse.
- Una de 2 GiB fue rechazada.
- Un TAR recursivo de 1 GiB y otro de 3,6 GiB fallaron después de unos 122 segundos.
Los datos están documentados en routes.py.

El problema adicional es que FilesClient.download_to() obtiene toda la respuesta en memoria antes de escribirla en disco.

Referencia: google_client.py.

Para proyectos pequeños no representa necesariamente un problema. Pero en proyectos grandes, como los que podrían utilizarse en una integración de desarrollo real, no es una buena base.

El proyecto ya dispone de recuperación manual por archivos individuales (fetch --path). Lo que falta es integrar una estrategia de retorno incremental o por archivos cuando el TAR completo no sea viable.

3. Qué significa esto para el cambio de paradigma
La conclusión más importante que saco es que no necesitamos crear un segundo aistudio-bridge ni volver a implementar sus capacidades de Google.

El proyecto ya tiene bastante infraestructura construida:
Capacidades que ya existen
Subida de proyectos locales mediante --local-repo.

Integración con Google Interactions API.

Ejecución en segundo plano y consulta de estado.

Continuación y cancelación remota.

Descarga, comparación y entrega en una rama local.

Integración de memoria local con Engram.


Lo que falta para el paradigma nuevo
Servidor MCP propio de aistudio-bridge.

Backend/runtime específico de Antigravity en Gentle Shell.

Integración con el TaskStore y las tarjetas nativas de Gentle Shell.

Captura y transferencia controlada del contexto Pi.

Gestión concurrente, aislamiento por tarea y recuperación robusta.


Por tanto, mantendría la arquitectura independiente de ambos proyectos:
- aistudio-bridge: servicio de ejecución remota, transferencia, estado, memoria y artefactos.
- Gentle Shell: selección de runtime, contexto Pi, TaskStore, tarjetas, eventos, experiencia de usuario y políticas de ejecución.
Y conservaría el CLI actual. El nuevo servidor MCP sería una interfaz adicional, no una sustitución de la interfaz existente.

cinco condiciones técnicas para cerrar la base:
Prioridad	Trabajo	Motivo
P0	Fijar el commit base y la identidad de cada snapshot	Evitar entregas que incorporen o borren cambios ajenos a la tarea
P0	Aislar las tareas concurrentes	Poder utilizar el runner desde Gentle Shell sin carreras ni contaminación
P0	Corregir la afirmación y la protección de --run-tests	No confundir un entorno de variables limpio con un sandbox real
P1	Completar o desactivar la ruta GCS	Evitar ofrecer un flujo de entrada sin entrega Git funcional
P1	Resolver la descarga incremental de workspaces grandes	No depender de un TAR completo que ya tiene límites medidos

Además, incorporaría al diseño la idempotencia de las llamadas a funciones y la reconciliación de interacciones completadas tras interrupciones.

Una última observación documental: la versión actual ya es 0.25.7, pero el CHANGELOG.md que he consultado termina en 0.25.5. Conviene mantener esos registros sincronizados; es pequeño, pero el historial de cambios es especialmente importante para un proyecto que pretende ofrecer recuperación fiable.
