-- 0042 · De qué país es la identificación fiscal
--
-- `accounts.cif` ya existe desde la 0040 y guarda el identificador. Lo que
-- faltaba es DE QUÉ PAÍS es, y sin eso no se puede ni validar de nuevo al
-- editarlo, ni ponerle la etiqueta correcta, ni volcarlo a una factura. El
-- número y su país son un solo dato y viajan juntos.
--
-- NO SE INFIERE DEL TELÉFONO. Es lo que hace `region_del_salon()` para leer un
-- número escrito en local, y allí está bien: un teléfono mal leído se ve. Un
-- identificador fiscal validado con las reglas del país equivocado se acepta o
-- se rechaza en silencio, y además en el alta todavía no hay teléfono.
--
-- ═══ LA REGLA (supabase/README.md) ═══
--
-- Columna nueva sobre `accounts`, que es una tabla en la que el backend inserta
-- —lo hace el trigger `salon_entra_en_una_cuenta`—, así que va ANULABLE y sin
-- valor por defecto obligatorio. El backend anterior sigue dando de alta sin
-- saber que existe. Obligatoria es en el formulario, que es donde toca.

begin;

alter table public.accounts add column cif_pais text;

comment on column public.accounts.cif_pais is
  'ISO 3166-1 alfa-2 del país de `cif`. Decide con qué reglas se valida y cómo '
  'se etiqueta: en España se comprueba la letra de control de verdad; fuera se '
  'acepta lo razonable hasta que se internacionalice. NULL = no consta, que es '
  'el estado de las cuentas anteriores a esta migración.';

-- BACKFILL MEDIDO, no supuesto. Solo se marca 'ES' donde ya hay un `cif` Y su
-- forma es la de un NIF, NIE o CIF español; el dígito de control lo comprueba
-- el backend, aquí basta la forma para no atribuirle un país a nadie por
-- casualidad. Las cuentas sin `cif` se quedan sin país: no consta, y decir que
-- son españolas sería inventárselo.
update public.accounts
set cif_pais = 'ES'
where cif is not null
  and upper(regexp_replace(cif, '[\s.\-/]', '', 'g')) ~
      '^([0-9]{8}[A-Z]|[XYZ][0-9]{7}[A-Z]|[ABCDEFGHJNPQRSUVW][0-9]{7}[0-9A-J])$';

create index accounts_sin_cif on public.accounts (id) where cif is null;

comment on index public.accounts_sin_cif is
  'Para localizar de un vistazo a quién hay que pedirle todavía el dato fiscal '
  'antes de emitirle la primera factura.';

commit;
