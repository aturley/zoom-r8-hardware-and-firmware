# Recovered names (task entries from static Task_Object array @0xc035d850, stride 68, fxn @+40)
TASKS=['(init task, unnamed?)','MainMenu','LCD','Panel','AudioCmdRcv','SerialHandler','Pad','SDriver','RhyHdr','EfxQFuncs','ARRM','RecApi',
 'TransportManager','ZStream_IO','ZStream','Tuner','TimeStretch','AIF_ManageTask','UsbMidiRxTask','AudioClkMng','UsbMidiTxTask','ti.sysbios.knl.Task.IdleTask']
def task_entries(M):
    out={}
    for i,n in enumerate(TASKS):
        a=0xc035d850+68*i
        out[M.u32(a+40)]=('task_'+n.split('.')[-1].strip('()').replace(' ','_').replace(',','').replace('?',''), M.u32(a+28), M.u32(a+8))
    return out
