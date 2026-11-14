#include <stdio.h>
#include <stdint.h>
uint32_t test(uint32_t, uint32_t*);
int main(void){uint32_t out[64]={0};uint32_t h=test(12345,out);printf("%08x",h);for(int i=0;i<64;i++)printf(" %x",out[i]);printf("\n");return 0;}
