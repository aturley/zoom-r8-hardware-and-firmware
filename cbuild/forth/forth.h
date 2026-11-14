#ifndef FORTH_H
#define FORTH_H
/* A small Forth: 32-bit cells, token-threaded colon definitions, interactive line evaluation.
   The host program provides the I/O and polling hooks below. */
void forth_init(void);
void forth_eval(const char *line, int n);     /* interpret one line; prints " ok" or an error */
extern volatile int forth_abort;              /* set by the host (Ctrl-C): stops the running word */
/* for host words (registered after forth_init) */
void forth_word(const char *name, void (*fn)(void));
void forth_push(int v);
int  forth_pop(void);
int  forth_failed(void);                      /* a pop hit an empty stack: the host word should stop */
void forth_type(const char *s);
void forth_dot(int v);
void forth_hex(unsigned v, int digits);
/* provided by the host */
void forth_emit(int c);
int  forth_key(void);                         /* -1 if no key yet */
void forth_poll(void);                        /* keep USB/LCD/power going during long words */
void forth_page(void);                        /* clear the screen console */
#endif
