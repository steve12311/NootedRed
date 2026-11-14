/* Load the pinned auxiliary collection before kernelmanagerd checks in.
 * No restart, sleep or retry loop. Always hand off to the original daemon.
 */
#include <sys/types.h>
#include <sys/sysctl.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
static void note(int fd,const char *msg,int value) {
    if(fd>=0){dprintf(fd,"%ld %s %d\n",(long)time(NULL),msg,value);fsync(fd);}
}
int main(void) {
    char build[64]={0},args[4096]={0};size_t n=sizeof(build);
    int ok=!sysctlbyname("kern.osversion",build,&n,NULL,0)&&!strcmp(build,"26B123");
    n=sizeof(args);
    ok=ok&&!sysctlbyname("kern.bootargs",args,&n,NULL,0)&&strstr(args,"-NRedDonor24")!=NULL;
    int fd=open("/var/log/nootedred-gpu-loader.log",O_WRONLY|O_CREAT|O_APPEND,0600);
    if(ok) {
        note(fd,"start AMD graphics AuxKC loader",0);
        pid_t p=fork();
        if(p==0) {
            setpgid(0,0);
            if(fd>=0){dup2(fd,1);dup2(fd,2);close(fd);}
            execl("/usr/local/libexec/nred-auxkcload","nred-auxkcload",(char *)0);
            perror("exec auxkcload");_exit(127);
        }
        if(p>0) {
            setpgid(p,p);int status=0,done=0;
            for(int i=0;i<45;i++) {
                if(waitpid(p,&status,WNOHANG)==p){done=1;break;}
                sleep(1);
            }
            if(!done){kill(-p,SIGKILL);kill(p,SIGKILL);note(fd,"loader timed out; no retry",45);}
            else {
                note(fd,"loader wait status",status);
                if(WIFEXITED(status)&&WEXITSTATUS(status)==0) {
                    int donefd=open("/var/run/nred-aux.done",O_WRONLY|O_CREAT|O_TRUNC,0600);
                    if(donefd>=0){dprintf(donefd,"loaded %ld\n",(long)time(NULL));fsync(donefd);close(donefd);}
                }
            }
        } else note(fd,"fork failed",-1);
    } else note(fd,"wrong build or donor flag absent; no AuxKC request",0);
    note(fd,"exec original kernelmanagerd",0);if(fd>=0)close(fd);
    execl("/usr/libexec/kernelmanagerd","/usr/libexec/kernelmanagerd",(char *)0);
    return 127;
}
