from transformers import AutoModel, AutoTokenizer
import pandas as pd
import torch
from sklearn.model_selection import train_test_split
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import os.path
import pickle
from torch.nn.utils.rnn import pad_sequence
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
TEST_SIZE = 0.2
DROP_OUT_P = 0.1
checkpoint = "codesage/codesage-small"

# Load tokenizer and model
tokenizer = AutoTokenizer.from_pretrained(checkpoint, trust_remote_code=True, add_eos_token=True)
model = AutoModel.from_pretrained(checkpoint, trust_remote_code=True)

df = pd.read_csv('full_data.csv')
df = df.head(500)
train_data = df['code'].values
train_labels = df['label'].values

train_data, valid_data, train_labels, valid_labels = train_test_split(train_data, train_labels, test_size=TEST_SIZE, random_state=42)
label_counts = df.iloc[:, 1].value_counts()
label_one = label_counts.get(1, 0)
label_zero = label_counts.get(0, 0)
gen_one = False
gen_zero = False
if label_one < label_zero:
    if label_one / label_zero < 2/3:
        gen_one = True
else:
   if label_zero / label_one < 2/3:
        gen_zero = True 

vocab_size = tokenizer.vocab_size

def encode_and_check(text, tokenizer, max_length=1024):
    inputs = tokenizer.encode(text, return_tensors="pt", truncation=True, max_length=max_length)
    if inputs.max() >= vocab_size:
        raise IndexError("Token ID out of range")
    return inputs

embeddings = []
embedding_gen = []
label_gen = []
file_path_embeddings = 'embeddings.pkl'
file_path_embeddings_gen = 'embedding_gen.pkl'
file_label_gen = 'label_gen.pkl'

if os.path.exists(file_path_embeddings) and os.path.exists(file_path_embeddings_gen):
    with open(file_path_embeddings, 'rb') as f:
        embeddings = pickle.load(f)
    with open(file_path_embeddings_gen, 'rb') as f:
        embedding_gen = pickle.load(f)
    with open(file_label_gen, 'rb') as f:
        label_gen = pickle.load(f)
else:
    idx_label = 0
    for code_snippet in train_data:
        inputs = encode_and_check(code_snippet, tokenizer)
        with torch.no_grad():
            embedding = model(inputs)[0]
        embeddings.append(embedding[0])
        if gen_one and train_labels[idx_label] == 1:
            prob_tensor = torch.empty_like(embedding[0]).bernoulli_(p = DROP_OUT_P) * (1 / (1 - DROP_OUT_P))
            embedding_gen.append(embedding[0]*prob_tensor)
            label_gen.append(1)
        if gen_zero and train_labels[idx_label] == 0:
            prob_tensor = torch.empty_like(embedding[0]).bernoulli_(p = DROP_OUT_P) * (1 / (1 - DROP_OUT_P))
            embedding_gen.append(embedding[0]*prob_tensor)
            label_gen.append(0) 
        idx_label += 1
        print(idx_label)   

    with open(file_path_embeddings, 'wb') as f:
        pickle.dump(embeddings, f)
    with open(file_path_embeddings_gen, 'wb') as f:
        pickle.dump(embedding_gen , f)
    with open(file_label_gen, 'wb') as f:
        pickle.dump(label_gen, f)








padded_embeddings = pad_sequence(embeddings, batch_first=True)
train_labels_tensor = torch.tensor(train_labels)

# Create TensorDataset and DataLoader
train_dataset = TensorDataset(padded_embeddings, train_labels_tensor)
train_loader = DataLoader(train_dataset, batch_size=8, shuffle=True)


class ImprovedLSTMClassifier(nn.Module):
    def __init__(self, embedding_dim, hidden_dim, output_dim, num_layers=1, dropout=0.5):
        super(ImprovedLSTMClassifier, self).__init__()
        self.lstm = nn.LSTM(embedding_dim, hidden_dim, num_layers=num_layers, batch_first=True, dropout=dropout)
        self.batch_norm = nn.BatchNorm1d(hidden_dim) 
        self.fc = nn.Linear(hidden_dim, output_dim)
        self.dropout = nn.Dropout(dropout)
        self.sigmoid = nn.Sigmoid()
        
    def attention_net(self, lstm_output, final_state):
        hidden = final_state.view(-1, hidden_dim, 1)  
        attn_weights = torch.bmm(lstm_output, hidden).squeeze(2)  
        soft_attn_weights = nn.functional.softmax(attn_weights, 1)
        new_hidden_state = torch.bmm(lstm_output.transpose(1, 2), soft_attn_weights.unsqueeze(2)).squeeze(2)
        return new_hidden_state
    
    def forward(self, x):
        lstm_out, (hn, cn) = self.lstm(x)
        attn_out = self.attention_net(lstm_out, hn[-1])
        attn_out = self.batch_norm(attn_out)  # Apply Batch Normalization
        out = self.dropout(attn_out)
        out = self.fc(out)
        out = self.sigmoid(out)
        return out


embedding_dim = 1024
hidden_dim = 64
output_dim = 1  

model_LSTM = ImprovedLSTMClassifier(embedding_dim, hidden_dim, output_dim, num_layers=2, dropout=0.3)


criterion = nn.BCELoss()
optimizer = optim.AdamW(model_LSTM.parameters(), lr=0.001, weight_decay=1e-5)


num_epochs = 10

for epoch in range(num_epochs):
    model_LSTM.train()
    epoch_loss = 0.0
    correct_predictions = 0
    total_predictions = 0
    
    for inputs, labels in train_loader:
        optimizer.zero_grad()
        outputs = model_LSTM(inputs)
        loss = criterion(outputs.squeeze(), labels.float())
        loss.backward()
        optimizer.step()
        
        epoch_loss += loss.item()
        
        # Calculate accuracy
        predictions = (outputs.squeeze() > 0.5).float()
        correct_predictions += (predictions == labels).sum().item()
        total_predictions += labels.size(0)
    
    accuracy = correct_predictions / total_predictions
    print(f'Epoch [{epoch+1}/{num_epochs}], Loss: {epoch_loss/len(train_loader):.4f}, Accuracy: {accuracy:.4f}')

torch.save(model_LSTM.state_dict(), 'model.pth')
    
    

valid_embeddings = []
valid_labels_tensor = torch.tensor(valid_labels)




for code_snippet in valid_data:
    inputs = encode_and_check(code_snippet, tokenizer)
    with torch.no_grad():
        embedding = model(inputs)[0]
    valid_embeddings.append(embedding[0])

padded_valid_embeddings = pad_sequence(valid_embeddings, batch_first=True)
valid_dataset = TensorDataset(padded_valid_embeddings, valid_labels_tensor)
valid_loader = DataLoader(valid_dataset, batch_size=8, shuffle=False)

def evaluate(model, dataloader):
    model.eval() 
    epoch_loss = 0.0
    correct_predictions = 0
    total_predictions = 0
    all_predictions = []
    all_labels = []
    
    with torch.no_grad():
        for inputs, labels in dataloader:
            outputs = model(inputs)
            loss = criterion(outputs.squeeze(), labels.float())
            epoch_loss += loss.item()
            
            # Calculate accuracy
            predictions = (outputs.squeeze() > 0.5).float()
            correct_predictions += (predictions == labels).sum().item()
            total_predictions += labels.size(0)
            
            all_predictions.extend(predictions.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())
    
    accuracy = correct_predictions / total_predictions
    precision = precision_score(all_labels, all_predictions)
    recall = recall_score(all_labels, all_predictions)
    f1 = f1_score(all_labels, all_predictions)
    
    return accuracy, precision, recall, f1, epoch_loss / len(dataloader)

accuracy, precision, recall, f1, val_loss = evaluate(model_LSTM, valid_loader)

print(f'Validation Loss: {val_loss:.4f}')
print(f'Validation Accuracy: {accuracy:.4f}')
print(f'Validation Precision: {precision:.4f}')
print(f'Validation Recall: {recall:.4f}')
print(f'Validation F1 Score: {f1:.4f}')
