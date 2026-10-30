-- Reservas online — la identidad pública del salón y el interruptor del widget.
--
-- QUÉ ABRE ESTA MIGRACIÓN: una página sin login donde el cliente reserva solo,
-- a la que se llega por un enlace o un QR. Hasta ahora un salón no tenía NINGUNA
-- identidad pública: se le nombra por su UUID, que sirve para una clave ajena y
-- no para una tarjeta impresa.
--
-- LO QUE ESTA MIGRACIÓN NO HACE, y conviene decirlo: no relaja ni una política
-- de RLS. La página es pública, pero quien entra a la base de datos sigue siendo
-- el backend con el token de un usuario del salón, exactamente igual que el
-- webhook del bot. Abrir la RLS habría sido la forma fácil y la equivocada: la
-- frontera de lo que se enseña la pone la proyección de cada endpoint, campo a
-- campo, no el motor de la base de datos.

-- ---------------------------------------------------------------------------
-- El slug: cómo se llama este salón de cara a la calle
-- ---------------------------------------------------------------------------
-- ES UN IDENTIFICADOR PÚBLICO, NO UN SECRETO. Se puede adivinar y no pasa nada:
-- es un escaparate, como el rótulo de la puerta. Lo que no puede es autorizar
-- por sí solo — saber el slug permite VER lo público de ese salón y pedir cita,
-- que es justo para lo que existe, y nada más.
--
-- CORTO A PROPÓSITO: un QR codifica caracteres, así que cuanta menos URL, menos
-- módulos y más gruesos los cuadros. Eso es lo que hace que se lea de un vistazo
-- desde una tarjeta pequeña o el escaparate. El UUID daría una URL tres veces
-- más larga, un QR mucho más denso y algo impresentable escrito en papel.
--
-- NULL = todavía no tiene. Se rellena al activar el widget, no al crear el
-- salón: un salón que nunca lo encienda no necesita ocupar un nombre público, y
-- así el espacio de nombres no se llena de salones que no lo usan.
--
-- El CHECK acota la forma antes de que llegue a una URL: minúsculas, dígitos y
-- guiones, sin guiones al principio ni al final, de 3 a 40 caracteres. Se valida
-- también en el backend —con la lista de palabras reservadas, que aquí no cabe—
-- pero esto es la red que impide que un slug con una barra o un punto entre por
-- una vía que se nos haya olvidado.
alter table public.salons
  add column slug text unique
  check (
    slug is null
    or (slug ~ '^[a-z0-9]([a-z0-9-]{1,38})[a-z0-9]$' and slug !~ '--')
  );

-- El índice lo crea ya el UNIQUE de arriba. La consulta que importa es
-- exactamente `where slug = $1` —la primera de cada visita a la página pública,
-- la que averigua de qué salón hablamos— y esa la resuelve el índice único.

-- ---------------------------------------------------------------------------
-- El interruptor
-- ---------------------------------------------------------------------------
-- APAGADO POR DEFECTO, y no es una precaución genérica: encenderlo publica en
-- internet los servicios, los precios y la disponibilidad real de un salón que
-- a lo mejor todavía no ha terminado de configurar su horario. Un salón sin
-- horario daría una página que dice «no hay huecos», que es peor que no tener
-- página. Que lo encienda quien haya mirado antes cómo se ve.
--
-- Y es un interruptor de verdad, no solo la ausencia de slug: un salón que se va
-- de vacaciones o que tiene la agenda llena querrá apagar el widget sin perder
-- su nombre público ni invalidar los QR que ya ha repartido.
alter table public.salons
  add column reservas_online_activo boolean not null default false;

-- ---------------------------------------------------------------------------
-- Lo que NO se añade, y por qué
-- ---------------------------------------------------------------------------
-- `employees.reservable_online`: la decisión es que por ahora se muestran los
-- empleados ACTIVOS que realizan el servicio, que es exactamente lo que ya
-- calcula `_empleados_que_lo_hacen`. La columna llegará cuando un salón pida
-- ocultar a alguien de la página sin darle de baja; añadirla hoy sería otra
-- casilla que nadie ha pedido en la pantalla de Empleados.
--
-- Zona horaria por salón: sigue siendo `Europe/Madrid` en el código
-- (`horario_activo.ZONA_SALON`). Es deuda conocida y anotada: el widget la
-- hereda, no la empeora en la base de datos.
--
-- Tabla de intentos para el anti-spam: no hace falta. Los dos contadores que
-- importan se resuelven contando sobre `appointments`, que ya tiene el canal y
-- el momento de creación.
